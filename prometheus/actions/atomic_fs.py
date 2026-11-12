"""Atomic, verified filesystem primitives for Prometheus 3.0."""
from __future__ import annotations

import difflib
import hashlib
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
    _is_link_or_junction,
    get_final_path_from_handle,
    verify_handle_containment,
)

MAX_EDITABLE_SIZE = 10 * 1024 * 1024    # 10 MB
MAX_WINDOW_LINES = 1000
DEFAULT_WINDOW_LINES = 200
CHUNK_SIZE = 64 * 1024                  # 64 KB


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


@dataclass
class _DiffHunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[str]


def parse_unified_diff(patch_text: str) -> list[_DiffHunk]:
    """Parse unified diff text into structured hunks."""
    hunks: list[_DiffHunk] = []
    lines = patch_text.splitlines(keepends=True)
    i = 0
    hunk_header_re = re.compile(r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@")

    while i < len(lines):
        line = lines[i]
        match = hunk_header_re.match(line)
        if match:
            old_start = int(match.group(1))
            old_count = int(match.group(2)) if match.group(2) is not None else 1
            new_start = int(match.group(3))
            new_count = int(match.group(4)) if match.group(4) is not None else 1
            hunk_lines: list[str] = []
            i += 1
            while i < len(lines) and not hunk_header_re.match(lines[i]):
                if lines[i].startswith(("---", "+++")) and not hunk_lines:
                    i += 1
                    continue
                hunk_lines.append(lines[i])
                i += 1
            hunks.append(_DiffHunk(old_start, old_count, new_start, new_count, hunk_lines))
        else:
            i += 1

    if not hunks:
        raise PatchError("No valid diff hunks found in patch text")
    return hunks


def apply_hunks_to_lines(original_lines: list[str], hunks: list[_DiffHunk]) -> list[str]:
    """Apply unified diff hunks with strict 0-fuzz context matching."""
    result: list[str] = []
    orig_idx = 0

    for hunk_num, hunk in enumerate(hunks, 1):
        target_orig_idx = hunk.old_start - 1
        if target_orig_idx < orig_idx:
            raise PatchError(f"Hunk {hunk_num} overlaps with previous changes at line {target_orig_idx + 1}")

        # Copy unmodified lines before hunk
        while orig_idx < target_orig_idx:
            if orig_idx >= len(original_lines):
                raise PatchError(f"Hunk {hunk_num} target line {target_orig_idx + 1} exceeds file length")
            result.append(original_lines[orig_idx])
            orig_idx += 1

        # Apply hunk lines
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
                result.append(content)
            elif hline.startswith("\\ No newline"):
                continue
            else:
                raise PatchError(f"Malformed line in hunk {hunk_num}: {hline!r}")

    # Copy remaining lines after last hunk
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

        # Open handle once and keep open during validation, hashing, and read
        with open(p, "rb") as f_in:
            if root is not None:
                verify_handle_containment(f_in.fileno(), root)
            sha256, total_bytes, is_binary = compute_file_sha256(f_in)
            if is_binary:
                raise AtomicFSError(f"Binary file detected: {p}")
            f_in.seek(0)
            raw = f_in.read()

        enc_info = detect_encoding_and_newline(raw)
        text = raw.decode(enc_info.encoding)
        all_lines = text.splitlines(keepends=True)
        total_lines = len(all_lines)

        # Enforce bounds
        start_line = max(1, start_line)
        line_count = max(0, min(line_count, MAX_WINDOW_LINES))

        if start_line > total_lines:
            window_lines = []
            end_line = start_line - 1
            truncated = False
        else:
            window_lines = all_lines[start_line - 1 : start_line - 1 + line_count]
            end_line = start_line - 1 + len(window_lines)
            truncated = end_line < total_lines

        content = "".join(window_lines)
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

            # Normalize target and replacement newlines to match target file convention
            if enc_info.newline == "\r\n":
                norm_target = target.replace("\r\n", "\n").replace("\n", "\r\n")
                norm_replacement = replacement.replace("\r\n", "\n").replace("\n", "\r\n")
            else:
                norm_target = target.replace("\r\n", "\n")
                norm_replacement = replacement.replace("\r\n", "\n")

            count = original_text.count(norm_target)
            if count == 0:
                raise MatchError(f"Target string not found in {p}")
            if count > 1:
                raise MatchError(f"Target string occurs {count} times in {p}; must be unique")

            new_text = original_text.replace(norm_target, norm_replacement, 1)

            # Re-normalize line endings
            if enc_info.newline == "\r\n":
                new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
            else:
                new_text = new_text.replace("\r\n", "\n")

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

            # Re-verify parent directory lineage immediately before replacement
            curr = p.parent
            while True:
                if _is_link_or_junction(curr):
                    raise PathSecurityError(f"Parent directory is a link or junction: {curr}")
                if curr.resolve() == root.resolve() or curr == curr.parent:
                    break
                curr = curr.parent

            # Commit replacement
            safe_replace_with_backoff(tmp_path, p)

            # Post-replacement handle containment verification
            with open(p, "rb") as f_post:
                verify_handle_containment(f_post.fileno(), root)
                post_sha256, _, _ = compute_file_sha256(f_post)
                if post_sha256 != new_sha256:
                    shutil.copy2(backup_path, p)
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

            new_lines = apply_hunks_to_lines(original_lines, hunks)
            new_text = "".join(new_lines)

            if enc_info.newline == "\r\n":
                new_text = new_text.replace("\r\n", "\n").replace("\n", "\r\n")
            else:
                new_text = new_text.replace("\r\n", "\n")

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

            # Re-verify parent directory lineage immediately before replacement
            curr = p.parent
            while True:
                if _is_link_or_junction(curr):
                    raise PathSecurityError(f"Parent directory is a link or junction: {curr}")
                if curr.resolve() == root.resolve() or curr == curr.parent:
                    break
                curr = curr.parent

            safe_replace_with_backoff(tmp_path, p)

            # Post-replacement handle containment verification
            with open(p, "rb") as f_post:
                verify_handle_containment(f_post.fileno(), root)
                post_sha256, _, _ = compute_file_sha256(f_post)
                if post_sha256 != new_sha256:
                    shutil.copy2(backup_path, p)
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

    def rollback_transaction(self, transaction_id: str) -> bool:
        """Rollback an uncommitted or failed transaction restoring original hash."""
        if not self.transactions_log.exists():
            return False

        target_record: dict = {}
        with open(self.transactions_log, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    if rec.get("tx_id") == transaction_id:
                        target_record.update(rec)
                except json.JSONDecodeError:
                    continue

        if not target_record or "target_path" not in target_record:
            return False

        target_path = Path(target_record["target_path"])
        backup_path = Path(target_record["backup_path"])
        old_sha256 = target_record.get("old_sha256")

        if not backup_path.exists():
            raise TransactionError(f"Backup file not found for rollback: {backup_path}")

        safe_replace_with_backoff(backup_path, target_path)

        restored_sha256, _, _ = compute_file_sha256(target_path)
        if old_sha256 and restored_sha256 != old_sha256:
            raise TransactionError(
                f"Rollback hash mismatch: expected {old_sha256}, actual {restored_sha256}"
            )

        tmp_path_str = target_record.get("tmp_path")
        if tmp_path_str:
            tmp_path = Path(tmp_path_str)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        self._log_transaction({
            "tx_id": transaction_id,
            "state": TransactionState.ROLLED_BACK.value,
            "target_path": str(target_path),
            "backup_path": str(backup_path),
            "updated_at": time.time(),
        })
        return True

    def recover_interrupted_transactions(self) -> list[str]:
        """Scan backup journal at startup, clean orphaned tmp files, and report/resolve state."""
        if not self.transactions_log.exists():
            return []

        transactions: dict[str, dict] = {}
        with open(self.transactions_log, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    tx_id = rec.get("tx_id")
                    if tx_id:
                        if tx_id not in transactions:
                            transactions[tx_id] = rec
                        else:
                            transactions[tx_id].update(rec)
                except json.JSONDecodeError:
                    continue

        recovered: list[str] = []

        for tx_id, rec in transactions.items():
            state = rec.get("state")
            if state in (TransactionState.COMMITTED.value, TransactionState.ROLLED_BACK.value):
                continue

            target_path_str = rec.get("target_path")
            backup_path_str = rec.get("backup_path")
            tmp_path_str = rec.get("tmp_path")
            old_sha256 = rec.get("old_sha256")
            new_sha256 = rec.get("new_sha256")

            if not target_path_str:
                continue

            target_path = Path(target_path_str)
            tmp_path = Path(tmp_path_str) if tmp_path_str else None
            backup_path = Path(backup_path_str) if backup_path_str else None

            if tmp_path and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

            if target_path.exists():
                curr_sha256, _, _ = compute_file_sha256(target_path)
                if state == TransactionState.VERIFIED.value:
                    if curr_sha256 == new_sha256:
                        self._log_transaction({
                            "tx_id": tx_id,
                            "state": TransactionState.COMMITTED.value,
                            "recovered_at": time.time(),
                        }, critical=False)
                        recovered.append(f"{tx_id}: committed")
                    elif curr_sha256 == old_sha256:
                        self._log_transaction({
                            "tx_id": tx_id,
                            "state": TransactionState.ROLLED_BACK.value,
                            "recovered_at": time.time(),
                        }, critical=False)
                        recovered.append(f"{tx_id}: rolled_back")
                    else:
                        if backup_path and backup_path.exists():
                            safe_replace_with_backoff(backup_path, target_path)
                            self._log_transaction({
                                "tx_id": tx_id,
                                "state": TransactionState.ROLLED_BACK.value,
                                "recovered_at": time.time(),
                            }, critical=False)
                            recovered.append(f"{tx_id}: restored_from_backup")
                else:  # STAGED, FAILED, etc.
                    if curr_sha256 == old_sha256:
                        self._log_transaction({
                            "tx_id": tx_id,
                            "state": TransactionState.ROLLED_BACK.value,
                            "recovered_at": time.time(),
                        }, critical=False)
                        recovered.append(f"{tx_id}: rolled_back")
                    else:
                        if backup_path and backup_path.exists():
                            safe_replace_with_backoff(backup_path, target_path)
                            self._log_transaction({
                                "tx_id": tx_id,
                                "state": TransactionState.ROLLED_BACK.value,
                                "recovered_at": time.time(),
                            }, critical=False)
                            recovered.append(f"{tx_id}: restored_from_backup")
            else:
                if backup_path and backup_path.exists():
                    safe_replace_with_backoff(backup_path, target_path)
                    self._log_transaction({
                        "tx_id": tx_id,
                        "state": TransactionState.ROLLED_BACK.value,
                        "recovered_at": time.time(),
                    }, critical=False)
                    recovered.append(f"{tx_id}: restored_missing_from_backup")

        return recovered
