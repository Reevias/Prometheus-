"""Atomic, verified filesystem primitives for Prometheus 3.0."""
from __future__ import annotations

import codecs
import difflib
import hashlib
import io
import json
import shutil
import os
import re
import stat
import sys
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from filelock import FileLock, Timeout

from prometheus.safety.paths import (
    PathBlocked,
    PathPolicy,
    _inside,
    _is_link_or_junction,
    get_final_path_from_handle,
    verify_handle_containment,
)

MAX_EDITABLE_SIZE = 10 * 1024 * 1024    # 10 MB
MAX_WINDOW_LINES = 1000
DEFAULT_WINDOW_LINES = 200
CHUNK_SIZE = 64 * 1024                  # 64 KB
LINE_PIECE_CHARS = 1024 * 1024          # read_window reads very long lines in pieces of this size
MAX_WINDOW_CHARS = 1024 * 1024          # read_window returns at most this many characters
TX_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")


class AtomicFSError(Exception):
    """Base exception for all atomic filesystem failures."""


class PathSecurityError(AtomicFSError):
    """Raised on path traversal, UNC paths, junctions, or .git access."""


class ConcurrencyError(AtomicFSError):
    """Raised when file on disk does not match expected SHA-256 or is locked."""


class MatchError(AtomicFSError):
    """Raised when target string is missing (0 matches) or ambiguous (>1 matches)."""


class PatchError(AtomicFSError):
    """Raised when a unified diff fails validation or hunk application."""


class TransactionError(AtomicFSError):
    """Raised when transaction commit or rollback fails."""


class JournalError(AtomicFSError):
    """Raised when transaction journaling fails to persist to disk."""


class TransactionState(str, Enum):
    INITIATED = "initiated"
    LOCKED = "locked"
    STAGED = "staged"
    VERIFIED = "verified"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass(frozen=True)
class FileEncodingInfo:
    encoding: str                    # e.g., "utf-8", "utf-8-sig", "latin-1"
    has_bom: bool                    # True if UTF-8 BOM detected
    newline: str                     # "\r\n" or "\n"


@dataclass(frozen=True)
class ReadWindowResult:
    path: Path
    content: str
    start_line: int
    end_line: int
    total_lines: int
    total_bytes: int
    sha256: str
    truncated: bool
    encoding: str
    newline: str


@dataclass
class EditResult:
    status: Literal["done", "failed"]
    path: Path
    old_sha256: str
    new_sha256: str
    backup_path: Path | None = None
    diff: str = ""
    error: str = ""
    transaction_id: str = ""


def compute_file_sha256(path_or_file: Any) -> tuple[str, int, bool]:
    """Compute SHA-256 hash, byte count, and binary status using chunked streaming.
    Supports either a path or an already open binary file object / descriptor.
    """
    hasher = hashlib.sha256()
    total_bytes = 0
    is_binary = False

    if hasattr(path_or_file, "read"):
        f = path_or_file
        f.seek(0)
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            total_bytes += len(chunk)
            hasher.update(chunk)
            if not is_binary and b"\x00" in chunk:
                is_binary = True
        f.seek(0)
        return hasher.hexdigest(), total_bytes, is_binary
    else:
        with open(path_or_file, "rb") as f:
            while True:
                chunk = f.read(CHUNK_SIZE)
                if not chunk:
                    break
                total_bytes += len(chunk)
                hasher.update(chunk)
                if not is_binary and b"\x00" in chunk:
                    is_binary = True
        return hasher.hexdigest(), total_bytes, is_binary


def detect_encoding_and_newline(raw: bytes) -> FileEncodingInfo:
    """Detect text encoding (UTF-8, UTF-8 BOM, Latin-1) and newline convention."""
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    if has_bom:
        encoding = "utf-8-sig"
    else:
        try:
            raw.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            encoding = "latin-1"

    newline = "\r\n" if b"\r\n" in raw else "\n"
    return FileEncodingInfo(encoding=encoding, has_bom=has_bom, newline=newline)


def safe_replace_with_backoff(src: Path, dst: Path, max_attempts: int = 5) -> None:
    """Atomically replace dst with src, retrying transient Windows file locks (WinError 32)."""
    if dst.exists():
        if sys.platform == "win32":
            try:
                attrs = os.stat(dst).st_file_attributes
                if attrs & stat.FILE_ATTRIBUTE_READONLY:
                    raise PermissionError(f"Target file is marked read-only: {dst}")
            except AttributeError:
                pass
        if not os.access(dst, os.W_OK):
            raise PermissionError(f"Target file is not writable: {dst}")

    backoffs = [0.025, 0.05, 0.1, 0.2, 0.4]
    last_err: Exception | None = None

    for attempt in range(max_attempts):
        try:
            os.replace(src, dst)
            return
        except (PermissionError, OSError) as err:
            last_err = err
            # Check for Windows ERROR_SHARING_VIOLATION / WinError 32
            if attempt < max_attempts - 1:
                time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
            else:
                raise last_err from None


def scan_text_file(f: Any) -> tuple[str, int, bool, FileEncodingInfo]:
    """One streaming pass over an open binary file: SHA-256, size, binary check, encoding and newline.

    Gives the same answers as compute_file_sha256 + detect_encoding_and_newline on the whole file, without
    holding more than one chunk in memory.
    """
    hasher = hashlib.sha256()
    total = 0
    is_binary = False
    utf8_ok = True
    decoder = codecs.getincrementaldecoder("utf-8")()
    crlf = False
    prev_cr = False
    head = b""
    f.seek(0)
    while True:
        chunk = f.read(CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        hasher.update(chunk)
        if len(head) < 3:
            head += chunk[: 3 - len(head)]
        if not is_binary and b"\x00" in chunk:
            is_binary = True
        if utf8_ok:
            try:
                decoder.decode(chunk)
            except UnicodeDecodeError:
                utf8_ok = False
        if not crlf and (b"\r\n" in chunk or (prev_cr and chunk.startswith(b"\n"))):
            crlf = True
        prev_cr = chunk.endswith(b"\r")
    if utf8_ok:
        try:
            decoder.decode(b"", final=True)
        except UnicodeDecodeError:
            utf8_ok = False
    f.seek(0)
    has_bom = head.startswith(b"\xef\xbb\xbf")
    encoding = "utf-8-sig" if has_bom else ("utf-8" if utf8_ok else "latin-1")
    info = FileEncodingInfo(encoding=encoding, has_bom=has_bom, newline="\r\n" if crlf else "\n")
    return hasher.hexdigest(), total, is_binary, info


def copy_file_metadata(original: Path, staged: Path) -> None:
    """Give the staged file the original's permissions (and owner, Windows attributes) before it replaces it."""
    try:
        st = os.stat(original, follow_symlinks=False)
    except FileNotFoundError:
        return
    if sys.platform == "win32":
        import ctypes
        keep = 0x2 | 0x4 | 0x20 | 0x2000   # hidden, system, archive, not-content-indexed
        attrs = getattr(st, "st_file_attributes", 0) & keep
        if attrs:
            ctypes.windll.kernel32.SetFileAttributesW(str(staged), attrs)
        return
    os.chmod(staged, stat.S_IMODE(st.st_mode))
    try:
        os.chown(staged, st.st_uid, st.st_gid)
    except (PermissionError, OSError, AttributeError):
        pass


def _ends_line(part: str) -> bool:
    """True if this piece (one element of str.splitlines(keepends=True)) ends with a line break."""
    return part.splitlines()[0] != part if part else False


def replace_pinned(src: Path, dst: Path, root: Path, max_attempts: int = 5) -> None:
    """Replace dst with src (both in the same folder), with the folder pinned by an open handle.

    The folder is opened once (not following a link or junction), checked to really be inside root, and the
    rename is done relative to that open folder, so swapping a parent folder for a link after the check can no
    longer redirect the write. Linux/macOS use a folder-relative rename (dir_fd); Windows uses NtSetInformationFile
    (FileRenameInformation) on a handle to the staged file with the pinned folder handle as RootDirectory.
    """
    if src.parent != dst.parent:
        raise PathSecurityError(f"Staged file and target must share a folder: {src} vs {dst}")
    canonical_root = Path(os.path.abspath(Path(root).expanduser())).resolve()
    curr = dst.parent
    while True:
        if _is_link_or_junction(curr):
            raise PathSecurityError(f"Parent directory is a link or junction: {curr}")
        if curr.resolve() == canonical_root or curr == curr.parent:
            break
        curr = curr.parent
    if sys.platform == "win32":
        if dst.exists() and not os.access(dst, os.W_OK):
            raise PermissionError(f"Target file is not writable: {dst}")
        _win_replace_pinned(src, dst, canonical_root, max_attempts)
        return
    if os.rename not in os.supports_dir_fd:
        safe_replace_with_backoff(src, dst, max_attempts=max_attempts)
        return
    if dst.exists() and not os.access(dst, os.W_OK):
        raise PermissionError(f"Target file is not writable: {dst}")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        dfd = os.open(str(dst.parent), flags)
    except OSError as err:
        raise PathSecurityError(f"Could not open the target's folder safely: {dst.parent}: {err}") from err
    try:
        real = get_final_path_from_handle(dfd)
        if not _inside(real, canonical_root):
            raise PathSecurityError(f"Target folder resolves outside {canonical_root}: {real}")
        backoffs = [0.025, 0.05, 0.1, 0.2, 0.4]
        for attempt in range(max_attempts):
            try:
                os.rename(src.name, dst.name, src_dir_fd=dfd, dst_dir_fd=dfd)   # POSIX rename replaces dst
                break
            except OSError:
                if attempt == max_attempts - 1:
                    raise
                time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
        try:
            os.fsync(dfd)   # make the rename itself survive a power cut
        except OSError:
            pass
    finally:
        os.close(dfd)


def _win_replace_pinned(src: Path, dst: Path, canonical_root: Path, max_attempts: int = 5) -> None:
    """Windows: rename src over dst relative to a pinned, checked handle of their folder."""
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ntdll = ctypes.WinDLL("ntdll")
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.GetFileAttributesW.argtypes = [wintypes.LPCWSTR]
    k32.GetFileAttributesW.restype = wintypes.DWORD
    k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
    k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD

    class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [("dwFileAttributes", wintypes.DWORD), ("ftCreationTime", wintypes.FILETIME),
                    ("ftLastAccessTime", wintypes.FILETIME), ("ftLastWriteTime", wintypes.FILETIME),
                    ("dwVolumeSerialNumber", wintypes.DWORD), ("nFileSizeHigh", wintypes.DWORD),
                    ("nFileSizeLow", wintypes.DWORD), ("nNumberOfLinks", wintypes.DWORD),
                    ("nFileIndexHigh", wintypes.DWORD), ("nFileIndexLow", wintypes.DWORD)]

    k32.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)]

    class IO_STATUS_BLOCK(ctypes.Structure):
        _fields_ = [("Status", ctypes.c_void_p), ("Information", ctypes.c_void_p)]

    INVALID = wintypes.HANDLE(-1).value
    DELETE, SYNCHRONIZE, FILE_LIST_DIRECTORY, FILE_READ_ATTRIBUTES = 0x00010000, 0x00100000, 0x0001, 0x0080
    SHARE_ALL = 0x1 | 0x2 | 0x4
    OPEN_EXISTING = 3
    FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT = 0x02000000, 0x00200000
    FILE_ATTRIBUTE_REPARSE_POINT = 0x400
    FileRenameInformation = 10

    def final_path(h) -> Path:
        buf = ctypes.create_unicode_buffer(32768)
        if not k32.GetFinalPathNameByHandleW(h, buf, 32768, 0):
            raise PathSecurityError(f"Could not read the real path of a handle (error {ctypes.get_last_error()})")
        text = buf.value
        if text.startswith("\\\\?\\UNC\\"):
            text = "\\\\" + text[8:]
        elif text.startswith("\\\\?\\"):
            text = text[4:]
        return Path(text)

    class FILE_ATTRIBUTE_TAG_INFO(ctypes.Structure):
        _fields_ = [("FileAttributes", wintypes.DWORD), ("ReparseTag", wintypes.DWORD)]

    k32.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]

    def is_reparse(h) -> bool:
        """True for links and junctions. Other reparse points (OneDrive cloud files, dedup) are ordinary folders."""
        info = FILE_ATTRIBUTE_TAG_INFO()
        if not k32.GetFileInformationByHandleEx(h, 9, ctypes.byref(info), ctypes.sizeof(info)):  # FileAttributeTagInfo
            raise PathSecurityError(f"Could not inspect a handle (error {ctypes.get_last_error()})")
        if not info.FileAttributes & FILE_ATTRIBUTE_REPARSE_POINT:
            return False
        return info.ReparseTag in (0xA0000003, 0xA000000C)   # mount point (junction), symlink

    # no FILE_SHARE_DELETE: nobody can rename or move the folder while it is pinned
    h_dir = k32.CreateFileW(str(dst.parent), FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES | SYNCHRONIZE, 0x1 | 0x2,
                            None, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, None)
    if not h_dir or h_dir == INVALID:
        raise PathSecurityError(f"Could not open the target's folder safely: {dst.parent} "
                                f"(error {ctypes.get_last_error()})")
    try:
        if is_reparse(h_dir):
            raise PathSecurityError(f"Target folder is a link or junction: {dst.parent}")
        real_dir = final_path(h_dir)
        if not _inside(real_dir, canonical_root):
            raise PathSecurityError(f"Target folder resolves outside {canonical_root}: {real_dir}")

        h_src = k32.CreateFileW(str(src), DELETE | FILE_READ_ATTRIBUTES | SYNCHRONIZE, SHARE_ALL, None,
                                OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, None)
        if not h_src or h_src == INVALID:
            raise OSError(f"Could not open the staged file {src} (error {ctypes.get_last_error()})")
        try:
            if is_reparse(h_src):
                raise PathSecurityError(f"Staged file is a link: {src}")
            if os.path.normcase(str(final_path(h_src).parent)) != os.path.normcase(str(real_dir)):
                raise PathSecurityError(f"Staged file is not in the target folder: {src}")

            name = dst.name.encode("utf-16-le")
            # FILE_RENAME_INFORMATION: ReplaceIfExists (padded to a pointer), RootDirectory, FileNameLength, FileName
            ptr = ctypes.sizeof(ctypes.c_void_p)
            off_len = 2 * ptr
            off_name = off_len + 4
            buf = ctypes.create_string_buffer(off_name + len(name) + 2)
            buf[0] = 1
            ctypes.c_void_p.from_buffer(buf, ptr).value = h_dir
            ctypes.c_ulong.from_buffer(buf, off_len).value = len(name)
            ctypes.memmove(ctypes.addressof(buf) + off_name, name, len(name))

            ntdll.NtSetInformationFile.argtypes = [wintypes.HANDLE, ctypes.POINTER(IO_STATUS_BLOCK), ctypes.c_void_p,
                                                   wintypes.ULONG, ctypes.c_int]
            ntdll.NtSetInformationFile.restype = ctypes.c_long
            ntdll.RtlNtStatusToDosError.argtypes = [ctypes.c_long]
            ntdll.RtlNtStatusToDosError.restype = wintypes.ULONG
            backoffs = [0.025, 0.05, 0.1, 0.2, 0.4]
            for attempt in range(max_attempts):
                iosb = IO_STATUS_BLOCK()
                status = ntdll.NtSetInformationFile(h_src, ctypes.byref(iosb), buf, len(buf.raw),
                                                    FileRenameInformation)
                if status >= 0:
                    return
                winerr = ntdll.RtlNtStatusToDosError(status)
                if attempt == max_attempts - 1 or winerr not in (5, 32, 33):   # access denied / sharing / lock
                    raise PermissionError(winerr, f"Could not replace {dst} (Windows error {winerr})")
                time.sleep(backoffs[min(attempt, len(backoffs) - 1)])
        finally:
            k32.CloseHandle(h_src)
    finally:
        k32.CloseHandle(h_dir)


@dataclass
class _DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


def parse_unified_diff(patch_text: str) -> list[_DiffHunk]:
    """Parse a single-file unified diff into hunks, strictly.

    Each hunk must hold exactly the line counts its @@ header states. File headers (---/+++/diff/index) are only
    skipped outside hunks, so an added line like "+++i;" or a removed "-- comment" is never mistaken for one.
    "\\ No newline at end of file" markers are kept and applied.
    """
    hunks: list[_DiffHunk] = []
    lines = patch_text.splitlines(keepends=True)
    hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")
    header_prefixes = ("--- ", "+++ ", "diff ", "index ", "new file mode", "deleted file mode", "similarity",
                       "rename ", "old mode", "new mode")
    i = 0
    while i < len(lines):
        line = lines[i]
        match = hunk_header_re.match(line)
        if not match:
            if hunks and line.strip() and not line.startswith(header_prefixes):
                raise PatchError(f"Unexpected line outside a hunk: {line.rstrip()!r}")
            i += 1
            continue
        old_start = int(match.group(1))
        old_count = int(match.group(2)) if match.group(2) is not None else 1
        new_start = int(match.group(3))
        new_count = int(match.group(4)) if match.group(4) is not None else 1
        i += 1
        old_seen = new_seen = 0
        hunk_lines: list[str] = []
        while old_seen < old_count or new_seen < new_count:
            if i >= len(lines):
                raise PatchError(f"Hunk at line {old_start} ends early: expected -{old_count} +{new_count}, "
                                 f"found -{old_seen} +{new_seen}")
            hl = lines[i]
            marker = hl[:1]
            if hl in ("\n", "\r\n"):          # a context line whose leading space was stripped
                hl, marker = " " + hl, " "
            if marker == " ":
                old_seen += 1
                new_seen += 1
            elif marker == "-":
                old_seen += 1
            elif marker == "+":
                new_seen += 1
            elif hl.startswith("\\"):
                hunk_lines.append(hl)
                i += 1
                continue
            else:
                raise PatchError(f"Malformed line in hunk at line {old_start}: {hl.rstrip()!r}")
            if old_seen > old_count or new_seen > new_count:
                raise PatchError(f"Hunk at line {old_start} has more lines than its header says")
            hunk_lines.append(hl)
            i += 1
        while i < len(lines) and lines[i].startswith("\\"):
            hunk_lines.append(lines[i])
            i += 1
        hunks.append(_DiffHunk(old_start, old_count, new_start, new_count, hunk_lines))

    if not hunks:
        raise PatchError("No valid diff hunks found in patch text")
    return hunks


def apply_hunks_to_lines(original_lines: list[str], hunks: list[_DiffHunk], newline: str = "\n") -> list[str]:
    """Apply unified diff hunks with strict 0-fuzz context matching.

    Unchanged lines keep their own line endings; added lines get `newline`.
    """
    result: list[str] = []
    orig_idx = 0

    for hunk_num, hunk in enumerate(hunks, 1):
        # a pure insertion (-N,0) goes after line N; everything else starts at line N
        target_orig_idx = hunk.old_start if hunk.old_count == 0 else hunk.old_start - 1
        if target_orig_idx < orig_idx:
            raise PatchError(f"Hunk {hunk_num} overlaps with previous changes at line {target_orig_idx + 1}")

        while orig_idx < target_orig_idx:
            if orig_idx >= len(original_lines):
                raise PatchError(f"Hunk {hunk_num} target line {target_orig_idx + 1} exceeds file length")
            result.append(original_lines[orig_idx])
            orig_idx += 1

        last = ""
        for hline in hunk.lines:
            marker = hline[:1]
            content = hline[1:]
            norm_content = content.rstrip("\r\n")

            if marker == " ":
                if orig_idx >= len(original_lines):
                    raise PatchError(f"Hunk {hunk_num} expected context beyond end of file: {norm_content!r}")
                actual = original_lines[orig_idx].rstrip("\r\n")
                if actual != norm_content:
                    raise PatchError(
                        f"Hunk {hunk_num} context mismatch at line {orig_idx + 1}: "
                        f"expected {norm_content!r}, found {actual!r}"
                    )
                result.append(original_lines[orig_idx])
                orig_idx += 1
            elif marker == "-":
                if orig_idx >= len(original_lines):
                    raise PatchError(f"Hunk {hunk_num} expected to delete line beyond EOF: {norm_content!r}")
                actual = original_lines[orig_idx].rstrip("\r\n")
                if actual != norm_content:
                    raise PatchError(
                        f"Hunk {hunk_num} deletion mismatch at line {orig_idx + 1}: "
                        f"expected {norm_content!r}, found {actual!r}"
                    )
                orig_idx += 1
            elif marker == "+":
                result.append(norm_content + newline)
            elif marker == "\\":
                if last == "+" and result:
                    result[-1] = result[-1].rstrip("\r\n")    # the new text ends without a newline
                continue
            else:
                raise PatchError(f"Malformed line in hunk {hunk_num}: {hline!r}")
            last = marker

    while orig_idx < len(original_lines):
        result.append(original_lines[orig_idx])
        orig_idx += 1

    return result


class AtomicFiles:
    """Production atomic, verified filesystem primitives for Prometheus 3.0."""

    def __init__(self, policy: PathPolicy, backup_dir: Path | None = None):
        self.policy = policy
        self.backup_dir = backup_dir or (policy.data_dir / "backups")
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.journals_dir = policy.data_dir / "journals"
        self.journals_dir.mkdir(parents=True, exist_ok=True)
        self.transactions_log = self.journals_dir / "transactions.jsonl"

    def _log_transaction(self, record: dict, critical: bool | None = None) -> None:
        """Append record to transactions.jsonl journal with flush and fsync."""
        state = record.get("state")
        if critical is None:
            critical = state in (TransactionState.STAGED.value, TransactionState.VERIFIED.value)

        line = json.dumps(record) + "\n"
        try:
            with open(self.transactions_log, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
        except OSError as err:
            if critical:
                raise JournalError(f"Failed to persist critical transaction journal entry ({state}): {err}") from err
            import logging
            logging.getLogger(__name__).warning("Non-critical journal write failed (%s): %s", state, err)

    def _get_lock(self, path: Path) -> FileLock:
        """Acquire target-adjacent FileLock without unlinking to prevent races."""
        lock_file = path.parent / f".{path.name}.lock"
        return FileLock(str(lock_file), timeout=3.0, preserve_lock_file=True)

    def read_window(
        self,
        path: Path | str,
        start_line: int = 1,
        line_count: int = DEFAULT_WINDOW_LINES,
        root: Path | None = None,
    ) -> ReadWindowResult:
        """Bounded line reader with 1-based indexing, chunked hashing, and binary check."""
        try:
            p = self.policy.check_read(path)
        except PathBlocked as err:
            raise PathSecurityError(str(err)) from err

        if root is not None:
            canonical_root = Path(os.path.abspath(Path(root).expanduser())).resolve()
            if not p.resolve().is_relative_to(canonical_root) and str(p.resolve()) != str(canonical_root):
                raise PathSecurityError(f"Path {p} is outside root {canonical_root}")

        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        start_line = max(1, start_line)
        line_count = max(0, min(line_count, MAX_WINDOW_LINES))
        last_wanted = start_line - 1 + line_count     # last line number kept

        # One open handle for containment, hashing and reading. The file is streamed: only the requested lines
        # are kept, so a huge file never sits in memory as a whole.
        with open(p, "rb") as f_in:
            if root is not None:
                verify_handle_containment(f_in.fileno(), root)
            sha256, total_bytes, is_binary, enc_info = scan_text_file(f_in)
            if is_binary:
                raise AtomicFSError(f"Binary file detected: {p}")
            text_in = io.TextIOWrapper(f_in, encoding=enc_info.encoding, newline="")
            try:
                pieces: list[str] = []      # the window's text, in pieces (joined once at the end)
                kept_chars = 0
                char_capped = False
                lines_in_window = 0
                total_lines = 0
                open_line = False          # the last piece stopped in the middle of a line
                cut_after_cr = False       # ...and that piece ended in "\r" (a "\r\n" may be split)

                def keep(text: str) -> None:
                    nonlocal kept_chars, char_capped
                    room = MAX_WINDOW_CHARS - kept_chars
                    if room <= 0:
                        char_capped = True
                        return
                    if len(text) > room:
                        text = text[:room]
                        char_capped = True
                    pieces.append(text)
                    kept_chars += len(text)

                while True:
                    piece = text_in.readline(LINE_PIECE_CHARS)
                    if not piece:
                        break
                    cut = len(piece) == LINE_PIECE_CHARS
                    if cut_after_cr and piece.startswith("\n"):
                        # "\r\n" split by the piece limit: the "\n" belongs to the line that ended in "\r"
                        if start_line <= total_lines <= last_wanted:
                            keep("\n")
                        piece = piece[1:]
                    cut_after_cr = False
                    for part in piece.splitlines(keepends=True):
                        if not open_line:
                            total_lines += 1
                            if start_line <= total_lines <= last_wanted and not char_capped:
                                lines_in_window += 1
                        if start_line <= total_lines <= last_wanted:
                            keep(part)
                        open_line = not _ends_line(part)
                    if cut and piece.endswith("\r"):
                        cut_after_cr = True
            finally:
                text_in.detach()

        if start_line > total_lines:
            pieces = []
            end_line = start_line - 1
            truncated = False
        else:
            end_line = start_line - 1 + lines_in_window
            truncated = end_line < total_lines or char_capped

        content = "".join(pieces)
        return ReadWindowResult(
            path=p,
            content=content,
            start_line=start_line,
            end_line=end_line,
            total_lines=total_lines,
            total_bytes=total_bytes,
            sha256=sha256,
            truncated=truncated,
            encoding=enc_info.encoding,
            newline=enc_info.newline,
        )

    def edit_exact(
        self,
        path: Path | str,
        target: str,
        replacement: str,
        root: Path,
        expected_sha256: str | None = None,
        run_id: str | None = None,
    ) -> EditResult:
        """Exact anchored string replacement with uniqueness check, atomic write, and rollback."""
        if not target:
            raise MatchError("Target string cannot be empty")

        try:
            p = self.policy.check_change(path, root)
        except PathBlocked as err:
            raise PathSecurityError(str(err)) from err

        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        file_size = p.stat().st_size
        if file_size > MAX_EDITABLE_SIZE:
            raise AtomicFSError(f"File exceeds 10 MB limit ({file_size} bytes)")

        tx_id = f"tx_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        backup_path = self.backup_dir / f"{tx_id}_{p.name}.bak"
        tmp_path = p.parent / f".{p.name}.tmp_{tx_id}"
        lock = self._get_lock(p)

        try:
            lock.acquire()
        except Timeout:
            raise ConcurrencyError(f"File is locked by another operation: {p}") from None

        try:
            # Open handle once and keep open for containment verification, hashing, and reading
            with open(p, "rb") as f_in:
                verify_handle_containment(f_in.fileno(), root)
                pre_sha256, file_size, is_binary = compute_file_sha256(f_in)
                if is_binary:
                    raise AtomicFSError(f"Cannot edit binary file: {p}")
                if file_size > MAX_EDITABLE_SIZE:
                    raise AtomicFSError(f"File exceeds 10 MB limit ({file_size} bytes)")
                if expected_sha256 and pre_sha256.lower() != expected_sha256.strip().lower():
                    raise ConcurrencyError(
                        f"SHA-256 conflict on {p}: expected {expected_sha256}, actual {pre_sha256}"
                    )
                f_in.seek(0)
                raw_bytes = f_in.read()
            enc_info = detect_encoding_and_newline(raw_bytes)
            original_text = raw_bytes.decode(enc_info.encoding)

            # Match the target with the file's main line ending, or failing that the other one (files can mix them).
            # Only the replaced text gets that line ending; the rest of the file is kept byte for byte.
            lf_target = target.replace("\r\n", "\n")
            lf_replacement = replacement.replace("\r\n", "\n")
            styles = [enc_info.newline] + [nl for nl in ("\n", "\r\n") if nl != enc_info.newline]
            match_at = -1
            for nl in styles:
                norm_target = lf_target.replace("\n", nl)
                match_at = original_text.find(norm_target)
                if match_at != -1:
                    norm_replacement = lf_replacement.replace("\n", nl)
                    break
                if "\n" not in lf_target:
                    break
            if match_at == -1:
                raise MatchError(f"Target string not found in {p}")
            if original_text.find(norm_target, match_at + 1) != -1:   # also catches overlapping matches
                raise MatchError(f"Target string occurs more than once in {p}; must be unique")

            new_text = original_text[:match_at] + norm_replacement + original_text[match_at + len(norm_target):]

            new_bytes = new_text.encode(enc_info.encoding)
            new_sha256 = hashlib.sha256(new_bytes).hexdigest()

            # Backup creation
            backup_path.write_bytes(raw_bytes)

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.STAGED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "tmp_path": str(tmp_path),
                "old_sha256": pre_sha256,
                "new_sha256": new_sha256,
                "created_at": time.time(),
            })

            # Staging tmp file
            with open(tmp_path, "wb") as f_tmp:
                f_tmp.write(new_bytes)
                f_tmp.flush()
                os.fsync(f_tmp.fileno())
            copy_file_metadata(p, tmp_path)

            # Verification
            staged_sha256, _, _ = compute_file_sha256(tmp_path)
            if staged_sha256 != new_sha256:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise AtomicFSError(f"Post-write hash mismatch on {tmp_path}")

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.VERIFIED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "updated_at": time.time(),
            })

            # Re-verify the parents and replace with the folder pinned (see replace_pinned)
            replace_pinned(tmp_path, p, root)

            # Post-replacement handle containment verification
            with open(p, "rb") as f_post:
                verify_handle_containment(f_post.fileno(), root)
                post_sha256, _, _ = compute_file_sha256(f_post)
            if post_sha256 != new_sha256:
                self._restore_backup(tx_id, backup_path, p, pre_sha256)
                raise PathSecurityError(f"Post-replacement integrity check failed on {p}")

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.COMMITTED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "old_sha256": pre_sha256,
                "new_sha256": new_sha256,
                "updated_at": time.time(),
            })

            orig_lines = original_text.splitlines(keepends=True)
            mod_lines = new_text.splitlines(keepends=True)
            diff_lines = list(difflib.unified_diff(orig_lines, mod_lines, fromfile=str(p), tofile=str(p)))
            diff_str = "".join(diff_lines)

            return EditResult(
                status="done",
                path=p,
                old_sha256=pre_sha256,
                new_sha256=new_sha256,
                backup_path=backup_path,
                diff=diff_str,
                transaction_id=tx_id,
            )

        except Exception as err:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.FAILED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "error": str(err),
                "updated_at": time.time(),
            }, critical=False)
            raise

        finally:
            try:
                lock.release()
            except Exception:
                pass

    def apply_diff(
        self,
        path: Path | str,
        patch_text: str,
        root: Path,
        expected_sha256: str | None = None,
        run_id: str | None = None,
    ) -> EditResult:
        """Strict 0-fuzz unified diff application with hunk pre-validation and rollback."""
        if not patch_text or not patch_text.strip():
            raise PatchError("Patch text cannot be empty")

        try:
            p = self.policy.check_change(path, root)
        except PathBlocked as err:
            raise PathSecurityError(str(err)) from err

        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"File not found: {p}")

        file_size = p.stat().st_size
        if file_size > MAX_EDITABLE_SIZE:
            raise AtomicFSError(f"File exceeds 10 MB limit ({file_size} bytes)")

        hunks = parse_unified_diff(patch_text)

        tx_id = f"tx_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        backup_path = self.backup_dir / f"{tx_id}_{p.name}.bak"
        tmp_path = p.parent / f".{p.name}.tmp_{tx_id}"
        lock = self._get_lock(p)

        try:
            lock.acquire()
        except Timeout:
            raise ConcurrencyError(f"File is locked by another operation: {p}") from None

        try:
            with open(p, "rb") as f_in:
                verify_handle_containment(f_in.fileno(), root)
                pre_sha256, file_size, is_binary = compute_file_sha256(f_in)
                if is_binary:
                    raise AtomicFSError(f"Cannot patch binary file: {p}")
                if file_size > MAX_EDITABLE_SIZE:
                    raise AtomicFSError(f"File exceeds 10 MB limit ({file_size} bytes)")
                if expected_sha256 and pre_sha256.lower() != expected_sha256.strip().lower():
                    raise ConcurrencyError(
                        f"SHA-256 conflict on {p}: expected {expected_sha256}, actual {pre_sha256}"
                    )
                f_in.seek(0)
                raw_bytes = f_in.read()
            enc_info = detect_encoding_and_newline(raw_bytes)
            original_text = raw_bytes.decode(enc_info.encoding)
            original_lines = original_text.splitlines(keepends=True)

            new_lines = apply_hunks_to_lines(original_lines, hunks, newline=enc_info.newline)
            new_text = "".join(new_lines)

            new_bytes = new_text.encode(enc_info.encoding)
            new_sha256 = hashlib.sha256(new_bytes).hexdigest()

            backup_path.write_bytes(raw_bytes)

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.STAGED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "tmp_path": str(tmp_path),
                "old_sha256": pre_sha256,
                "new_sha256": new_sha256,
                "created_at": time.time(),
            })

            with open(tmp_path, "wb") as f_tmp:
                f_tmp.write(new_bytes)
                f_tmp.flush()
                os.fsync(f_tmp.fileno())
            copy_file_metadata(p, tmp_path)

            staged_sha256, _, _ = compute_file_sha256(tmp_path)
            if staged_sha256 != new_sha256:
                if tmp_path.exists():
                    tmp_path.unlink()
                raise AtomicFSError(f"Post-write hash mismatch on {tmp_path}")

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.VERIFIED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "updated_at": time.time(),
            })

            # Re-verify the parents and replace with the folder pinned (see replace_pinned)
            replace_pinned(tmp_path, p, root)

            # Post-replacement handle containment verification
            with open(p, "rb") as f_post:
                verify_handle_containment(f_post.fileno(), root)
                post_sha256, _, _ = compute_file_sha256(f_post)
            if post_sha256 != new_sha256:
                self._restore_backup(tx_id, backup_path, p, pre_sha256)
                raise PathSecurityError(f"Post-replacement integrity check failed on {p}")

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.COMMITTED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "old_sha256": pre_sha256,
                "new_sha256": new_sha256,
                "updated_at": time.time(),
            })

            diff_lines = list(difflib.unified_diff(original_lines, new_lines, fromfile=str(p), tofile=str(p)))
            diff_str = "".join(diff_lines)

            return EditResult(
                status="done",
                path=p,
                old_sha256=pre_sha256,
                new_sha256=new_sha256,
                backup_path=backup_path,
                diff=diff_str,
                transaction_id=tx_id,
            )

        except Exception as err:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

            self._log_transaction({
                "tx_id": tx_id,
                "state": TransactionState.FAILED.value,
                "target_path": str(p),
                "backup_path": str(backup_path),
                "error": str(err),
                "updated_at": time.time(),
            }, critical=False)
            raise

        finally:
            try:
                lock.release()
            except Exception:
                pass

    # ---- journal-driven rollback and recovery -------------------------------------------------------------
    # The journal is a file on disk, so nothing read from it is trusted: every path is checked against the path
    # policy and the expected file names, and a backup is only restored if its SHA-256 matches the hash recorded
    # before the edit. A file whose content is neither the old nor the new version is left alone (someone changed
    # it since), and reported instead of overwritten.

    def _read_journal(self) -> dict[str, dict]:
        transactions: dict[str, dict] = {}
        if not self.transactions_log.exists():
            return transactions
        with open(self.transactions_log, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                tx_id = rec.get("tx_id")
                if isinstance(tx_id, str) and tx_id:
                    transactions.setdefault(tx_id, {}).update(rec)
        return transactions

    def _checked_paths(self, tx_id: str, rec: dict) -> tuple[Path, Path | None, Path | None]:
        """Validate the journal's paths for one transaction. Raises TransactionError if any is unsafe."""
        if not TX_ID_RE.match(tx_id):
            raise TransactionError(f"Journal entry has an invalid transaction id: {tx_id!r}")
        target_str = rec.get("target_path")
        if not isinstance(target_str, str) or not target_str:
            raise TransactionError(f"{tx_id}: journal entry has no target path")
        root = self.policy.root_for(target_str)
        if root is None:
            raise TransactionError(f"{tx_id}: target is outside every allowed folder: {target_str}")
        try:
            target = self.policy.check_change(target_str, root)
        except PathBlocked as err:
            raise TransactionError(f"{tx_id}: target not allowed: {err}") from err
        if _is_link_or_junction(target):
            raise TransactionError(f"{tx_id}: target is a link: {target}")

        backup = None
        backup_str = rec.get("backup_path")
        if isinstance(backup_str, str) and backup_str:
            raw = Path(backup_str)
            backup_root = self.backup_dir.resolve()
            if _is_link_or_junction(raw) or not raw.name.startswith(f"{tx_id}_"):
                raise TransactionError(f"{tx_id}: backup path is not one of Prometheus's backups: {raw}")
            resolved = Path(os.path.abspath(raw)).resolve()
            if resolved.parent != backup_root:
                raise TransactionError(f"{tx_id}: backup is outside the backup folder: {raw}")
            backup = resolved

        tmp = None
        tmp_str = rec.get("tmp_path")
        if isinstance(tmp_str, str) and tmp_str:
            raw = Path(tmp_str)
            expected = target.parent / f".{target.name}.tmp_{tx_id}"
            if _norm_path(raw) != _norm_path(expected) or _is_link_or_junction(raw):
                raise TransactionError(f"{tx_id}: staged-file path doesn't match the target: {raw}")
            tmp = expected
        return target, backup, tmp

    def _restore_backup(self, tx_id: str, backup: Path, target: Path, old_sha256: str) -> None:
        """Copy a verified backup over target (the backup itself is kept)."""
        if not old_sha256:
            raise TransactionError(f"{tx_id}: no recorded hash for the backup, so it isn't trusted")
        backup_sha, _, _ = compute_file_sha256(backup)
        if backup_sha != old_sha256:
            raise TransactionError(f"{tx_id}: backup is damaged or was changed (hash mismatch), not restored")
        root = self.policy.root_for(target)
        staged = target.parent / f".{target.name}.restore_{tx_id}"
        try:
            with open(backup, "rb") as src, open(staged, "xb") as out:
                shutil.copyfileobj(src, out, CHUNK_SIZE)
                out.flush()
                os.fsync(out.fileno())
            if compute_file_sha256(staged)[0] != old_sha256:
                raise TransactionError(f"{tx_id}: restored copy doesn't match the backup")
            replace_pinned(staged, target, root)
        finally:
            if staged.exists():
                try:
                    staged.unlink()
                except OSError:
                    pass
        restored_sha, _, _ = compute_file_sha256(target)
        if restored_sha != old_sha256:
            raise TransactionError(f"Rollback hash mismatch: expected {old_sha256}, actual {restored_sha}")

    def _mark(self, tx_id: str, state: TransactionState, target: Path, note: str = "") -> None:
        self._log_transaction({
            "tx_id": tx_id,
            "state": state.value,
            "target_path": str(target),
            "note": note,
            "updated_at": time.time(),
        }, critical=False)

    def rollback_transaction(self, transaction_id: str) -> bool:
        """Undo one transaction: put back the file as it was before the edit, from its verified backup."""
        rec = self._read_journal().get(transaction_id)
        if not rec or "target_path" not in rec:
            return False
        target, backup, tmp = self._checked_paths(transaction_id, rec)
        old_sha256 = rec.get("old_sha256") or ""
        new_sha256 = rec.get("new_sha256") or ""
        if backup is None or not backup.exists():
            raise TransactionError(f"Backup file not found for rollback: {backup}")

        lock = self._get_lock(target)
        try:
            lock.acquire()
        except Timeout:
            raise ConcurrencyError(f"File is locked by another operation: {target}") from None
        try:
            if target.exists():
                current = compute_file_sha256(target)[0]
                if current not in (old_sha256, new_sha256):
                    raise ConcurrencyError(
                        f"{target} was changed after this edit; not overwriting it (current hash {current})"
                    )
            self._restore_backup(transaction_id, backup, target, old_sha256)
            if tmp is not None and tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        finally:
            try:
                lock.release()
            except Exception:
                pass

        self._log_transaction({
            "tx_id": transaction_id,
            "state": TransactionState.ROLLED_BACK.value,
            "target_path": str(target),
            "backup_path": str(backup),
            "updated_at": time.time(),
        })
        return True

    def recover_interrupted_transactions(self) -> list[str]:
        """At startup: finish or undo edits that were cut off (crash, power cut), and clean staged files.

        Every result is reported as "<tx_id>: <what happened>". Unsafe or unclear entries are skipped and reported,
        never acted on.
        """
        recovered: list[str] = []
        for tx_id, rec in self._read_journal().items():
            state = rec.get("state")
            if state in (TransactionState.COMMITTED.value, TransactionState.ROLLED_BACK.value):
                continue
            if not rec.get("target_path"):
                continue
            try:
                target, backup, tmp = self._checked_paths(tx_id, rec)
            except (TransactionError, PathBlocked) as err:
                recovered.append(f"{tx_id}: skipped_unsafe_journal_entry ({err})")
                continue

            old_sha256 = rec.get("old_sha256") or ""
            new_sha256 = rec.get("new_sha256") or ""

            # the same lock as edits, so an edit that is still running is never mistaken for a crashed one
            lock = self._get_lock(target)
            try:
                lock.acquire(timeout=0.5)
            except Timeout:
                recovered.append(f"{tx_id}: skipped_busy (an edit of this file is running)")
                continue
            try:
                if tmp is not None and tmp.exists():
                    try:
                        tmp.unlink()
                    except OSError:
                        pass
                recovered.extend(self._recover_one(tx_id, state, target, backup, old_sha256, new_sha256))
            finally:
                try:
                    lock.release()
                except Exception:
                    pass

        return recovered

    def _recover_one(self, tx_id: str, state: str | None, target: Path, backup: Path | None,
                     old_sha256: str, new_sha256: str) -> list[str]:
        recovered: list[str] = []
        try:
            if target.exists():
                current = compute_file_sha256(target)[0]
                if old_sha256 and current == old_sha256:
                    self._mark(tx_id, TransactionState.ROLLED_BACK, target, "file unchanged")
                    recovered.append(f"{tx_id}: rolled_back")
                elif new_sha256 and current == new_sha256 and state == TransactionState.VERIFIED.value:
                    self._mark(tx_id, TransactionState.COMMITTED, target, "replacement had finished")
                    recovered.append(f"{tx_id}: committed")
                elif (new_sha256 and current == new_sha256 and state == TransactionState.FAILED.value
                      and backup is not None and backup.exists()):
                    # the edit replaced the file and then reported a failure: undo it, as reported. (A STAGED
                    # entry can't have replaced the file: the replace only happens after VERIFIED is logged.)
                    self._restore_backup(tx_id, backup, target, old_sha256)
                    self._mark(tx_id, TransactionState.ROLLED_BACK, target, "unconfirmed write undone")
                    recovered.append(f"{tx_id}: restored_from_backup")
                elif not old_sha256:
                    # failed before anything was staged: nothing to undo
                    self._mark(tx_id, TransactionState.ROLLED_BACK, target, "nothing was staged")
                    recovered.append(f"{tx_id}: rolled_back")
                else:
                    recovered.append(f"{tx_id}: skipped_changed_since (file has other content; left as is)")
            elif backup is not None and backup.exists():
                self._restore_backup(tx_id, backup, target, old_sha256)
                self._mark(tx_id, TransactionState.ROLLED_BACK, target, "missing file restored")
                recovered.append(f"{tx_id}: restored_missing_from_backup")
        except (TransactionError, PathBlocked, OSError) as err:
            recovered.append(f"{tx_id}: failed ({err})")

        return recovered


def _norm_path(p: Path) -> str:
    return os.path.normcase(os.path.abspath(p))
