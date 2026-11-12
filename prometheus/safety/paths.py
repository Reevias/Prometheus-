"""Which files Prometheus may read or change."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

BLOCKED_UNDER_HOME = (
    "AppData",
    ".ssh",
    ".gnupg",
    ".aws",
    ".config/google-chrome",
    ".config/chromium",
    ".mozilla",
    ".local/share/keyrings",
    ".password-store",
)
BLOCKED_NAME_PREFIXES = ("keepass",)

WINDOWS_RESERVED_NAMES = frozenset({
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
    # Windows also treats superscript 1-3 as device numbers (COM¹ is the same device as COM1)
    *(f"{dev}{sup}" for dev in ("COM", "LPT") for sup in ("\u00b9", "\u00b2", "\u00b3")),
})

# Never usable, even inside an extra folder the owner added (for example a whole drive).
SYSTEM_DIRS = tuple(p for p in (
    os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramData", r"C:\ProgramData"),
    r"C:\$Recycle.Bin", r"C:\System Volume Information", "/etc", "/bin", "/sbin", "/usr", "/boot", "/proc", "/sys",
    "/dev", "/System", "/Library") if p)


class PathBlocked(Exception):
    """Raised when a path is outside what Prometheus may touch."""


class PathInspectionError(PathBlocked):
    """Raised when link, junction, or file metadata cannot be safely inspected."""


def _norm(p: Path | str) -> str:
    return os.path.normcase(str(p))


def _inside(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


def _check_raw_path(p: Path | str) -> str:
    """Pre-screen raw path string for UNC, device namespaces, reserved names, and alternate streams.

    Must be evaluated BEFORE invoking os.path or pathlib methods that could
    trigger Win32 network authentication (SMB/NTLM leaks) or DOS device hangs.
    """
    raw = str(p).strip().strip('"\'')
    if not raw:
        raise PathBlocked("Empty path is not allowed.")

    norm_slashes = raw.replace("/", "\\")

    # 1. Reject UNC and device namespace forms: \\server, //server, \\?\UNC\, \\.\, etc.
    if norm_slashes.startswith(("\\\\", "//")):
        raise PathBlocked(f"UNC and device namespace paths are not allowed: {raw}")
    if "\\UNC\\" in norm_slashes.upper() or "/UNC/" in raw.upper():
        raise PathBlocked(f"UNC paths are not allowed: {raw}")
    if norm_slashes.startswith(("\\\\.\\", "\\\\?\\", "//./", "//?/")):
        raise PathBlocked(f"Device namespace paths are not allowed: {raw}")

    # 2. Reject NTFS Alternate Data Streams (e.g. file.txt:hidden or drive:dir:stream)
    # On Windows, drive letter format is C: (colon at index 1). Any other colon is an ADS stream.
    colon_idx = raw.find(":")
    if colon_idx != -1:
        if colon_idx != 1 or ":" in raw[2:]:
            raise PathBlocked(f"NTFS alternate data streams are not allowed: {raw}")

    # 3. Reject Windows reserved DOS device names (CON, PRN, AUX, NUL, COM1-9, LPT1-9)
    parts = [part for part in norm_slashes.split("\\") if part]
    for part in parts:
        stem = part.split(".")[0].rstrip(" .").strip().upper()   # Windows ignores trailing dots and spaces
        if stem in WINDOWS_RESERVED_NAMES:
            raise PathBlocked(f"Windows reserved device name is not allowed: {part}")

    return raw


def _has_git_component(p: Path | str) -> bool:
    """Check if any path component refers to .git directory internals."""
    parts = Path(p).parts
    for part in parts:
        cleaned = part.strip().rstrip(".").lower()
        if cleaned == ".git":
            return True
    return False


def _is_link_or_junction(p: Path | str) -> bool:
    """Detect if path is a symlink, NTFS junction, or mount point reparse tag.

    Fails closed: If inspection cannot be completed due to permissions or OS errors,
    raises PathInspectionError so callers reject the path.
    Only returns False when the entry provably does not exist (FileNotFoundError)
    or when metadata inspection succeeds and confirms the entry is not a link/junction.
    """
    path = Path(p)
    try:
        try:
            if path.is_symlink():
                return True
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as err:
            raise PathInspectionError(f"Failed to inspect symlink status for {p}: {err}") from err

        if hasattr(path, "is_junction"):
            try:
                if path.is_junction():
                    return True
            except FileNotFoundError:
                return False
            except (OSError, ValueError) as err:
                raise PathInspectionError(f"Failed to inspect junction status for {p}: {err}") from err

        try:
            st = os.lstat(str(p))
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as err:
            raise PathInspectionError(f"Failed to inspect path metadata for {p}: {err}") from err

        if stat.S_ISLNK(st.st_mode):
            return True
        tag = getattr(st, "st_reparse_tag", 0)
        mount_point = getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", 0xA0000003)
        symlink = getattr(stat, "IO_REPARSE_TAG_SYMLINK", 0xA000000C)
        if tag in (mount_point, symlink):
            return True
        return False
    except PathInspectionError:
        raise
    except Exception as err:
        raise PathInspectionError(f"Unexpected error inspecting path {p}: {err}") from err


def get_final_path_from_handle(fd: int) -> Path:
    """Retrieve the canonical file path from an open OS file descriptor.

    On Windows, queries the kernel directly via GetFinalPathNameByHandleW
    to resolve the true disk path, bypassing symlinks, junctions, and TOCTOU races.
    On POSIX systems, inspects /proc/self/fd or falls back to os.path.realpath.
    """
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetFinalPathNameByHandleW.argtypes = [wintypes.HANDLE, wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD]
        k32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
        handle = msvcrt.get_osfhandle(fd)
        size = 32768
        buf = ctypes.create_unicode_buffer(size)
        # 0 = VOLUME_NAME_DOS. A result >= size means the buffer was too small (nothing written): fail closed.
        res = k32.GetFinalPathNameByHandleW(handle, buf, size, 0)
        if res == 0 or res >= size or not buf.value:
            raise OSError(ctypes.get_last_error(), "GetFinalPathNameByHandleW failed to query file handle")
        path_str = buf.value
        # Strip Windows device namespace prefix if present (e.g. \\?\C:\... -> C:\...)
        if path_str.startswith("\\\\?\\UNC\\"):
            path_str = "\\\\" + path_str[8:]
        elif path_str.startswith("\\\\?\\"):
            path_str = path_str[4:]
        return Path(path_str).resolve()
    if sys.platform == "darwin":
        import fcntl
        raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(1024))   # macOS: /dev/fd/N doesn't resolve to the file
        return Path(os.fsdecode(raw.split(b"\0", 1)[0])).resolve()
    fd_path = f"/proc/self/fd/{fd}"
    if os.path.exists(fd_path):
        return Path(os.path.realpath(fd_path))
    return Path(os.path.realpath(f"/dev/fd/{fd}"))


def verify_handle_containment(fd: int, root: Path | str) -> Path:
    """Verify that an open file descriptor points strictly inside an authorized root.

    Mitigates TOCTOU race conditions where a path was swapped to a junction
    or symlink between path validation and file opening.
    """
    canonical_root = Path(os.path.abspath(Path(root).expanduser())).resolve()
    final_path = get_final_path_from_handle(fd)

    if _has_git_component(final_path):
        raise PathBlocked(f"Open file handle resolves to .git internals: {final_path}")
    if not _inside(final_path, canonical_root):
        raise PathBlocked(f"Open file handle resolves outside authorized root {canonical_root}: {final_path}")
    return final_path


def extra_root_problem(folder: str, home: Path) -> str:
    """Why a folder can't be added as an extra place, or '' if it can."""
    raw = (folder or "").strip().strip('"')
    if not raw:
        return "Type a folder or drive, for example D:\\ or D:\\Photos."
    try:
        _check_raw_path(raw)
    except PathBlocked as err:
        return str(err)
    path = Path(os.path.abspath(Path(raw).expanduser()))
    if not path.is_absolute() or not path.exists() or not path.is_dir():
        return f"{raw} doesn't exist or isn't a folder."
    try:
        if _is_link_or_junction(path):
            return "Symlinks or junctions cannot be added as an extra place."
    except PathBlocked as err:
        return f"Cannot safely verify path: {err}"
    resolved = path.resolve()
    if _has_git_component(resolved):
        return "Git repository internals (.git) cannot be added as an extra place."
    system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\/") + os.sep
    if _norm(resolved) in (_norm(Path(system_drive)), _norm(Path("/"))):
        return "The whole system drive can't be added. Add a folder on it instead, or another drive."
    for sys_dir in SYSTEM_DIRS:
        if _inside(resolved, Path(sys_dir)) or _inside(Path(sys_dir), resolved):
            return f"{resolved} holds system files, so it can't be added."
    if _inside(resolved, Path(home).resolve()):
        return "That is already inside your user folder, which Prometheus can use."
    return ""


class PathPolicy:
    def __init__(self, home: Path, data_dir: Path, code_dir: Path, protected: tuple[Path, ...] | list = (),
                 extra_roots: tuple[Path, ...] | list = ()):
        self.home = Path(home).resolve()
        self.extra_roots = [Path(p).resolve() for p in extra_roots]   # more folders or drives the owner allowed
        self.protected = [Path(p).resolve() for p in protected]   # readable, but agents never change them
        self.data_dir = Path(data_dir).resolve()
        self.code_dir = Path(code_dir).resolve()
        self._blocked = [self.home / rel for rel in BLOCKED_UNDER_HOME] + [self.code_dir]

    def _resolve(self, p: Path | str) -> Path:
        _check_raw_path(p)
        return Path(os.path.abspath(Path(p).expanduser())).resolve()

    def is_blocked(self, p: Path | str) -> bool:
        try:
            _check_raw_path(p)
            path = self._resolve(p)
        except PathBlocked:
            return True
        if _has_git_component(Path(p)) or _has_git_component(path):
            return True
        if not _inside(path, self.home):
            if any(_inside(path, r) for r in self.extra_roots):
                return any(_inside(path, b) for b in self._blocked + self._system()) or self._blocked_name(path)
            return True
        if _inside(path, self.data_dir):
            name = os.path.normcase(path.name).lower()   # keys, password hash and their temp/backup copies
            return path.parent == self.data_dir and (name.startswith(".env") or name.startswith("settings."))
        if any(_inside(path, b) for b in self._blocked):
            return True
        rel_parts = path.relative_to(self.home).parts if _norm(path) != _norm(self.home) else ()
        return any(os.path.normcase(part).lower().startswith(BLOCKED_NAME_PREFIXES) for part in rel_parts)

    def _blocked_name(self, path: Path) -> bool:
        return any(part.lower().startswith(BLOCKED_NAME_PREFIXES) for part in path.parts)

    def _system(self) -> list[Path]:
        return [Path(p) for p in SYSTEM_DIRS] + [self.data_dir]

    def roots(self) -> list[Path]:
        """Every place Prometheus may work in: the home folder first, then the extra ones."""
        return [self.home] + [r for r in self.extra_roots if r != self.home]

    def root_for(self, p: Path | str) -> Path | None:
        """The allowed place that holds this path (the most specific one), or None."""
        try:
            path = self._resolve(p)
        except PathBlocked:
            return None
        best = None
        for r in self.roots():
            if _inside(path, r) and (best is None or len(str(r)) > len(str(best))):
                best = r
        return best

    def check_read(self, p: Path | str) -> Path:
        _check_raw_path(p)
        if _has_git_component(Path(p)):
            raise PathBlocked(f"Direct access to .git internals is not allowed: {p}")
        path = self._resolve(p)
        if _has_git_component(path):
            raise PathBlocked(f"Direct access to .git internals is not allowed: {p}")
        if self.is_blocked(path):
            raise PathBlocked(f"Not allowed: {p}")
        return path

    def check_change(self, p: Path | str, task_root: Path) -> Path:
        _check_raw_path(p)
        _check_raw_path(task_root)

        if _has_git_component(Path(p)):
            raise PathBlocked(f"Direct access to .git internals is not allowed: {p}")
        if _has_git_component(Path(task_root)):
            raise PathBlocked(f"Direct access to .git internals is not allowed: {task_root}")

        if _is_link_or_junction(task_root):
            raise PathBlocked(f"Task root cannot be a symlink or junction: {task_root}")

        root = self._resolve(task_root)
        if _has_git_component(root):
            raise PathBlocked(f"Direct access to .git internals is not allowed: {task_root}")
        if self.is_blocked(root):
            raise PathBlocked(f"Not allowed to change files in: {task_root}")
        if _is_link_or_junction(root):
            raise PathBlocked(f"Task root cannot be a symlink or junction: {task_root}")

        raw_target = Path(os.path.abspath(Path(p).expanduser()))
        curr = raw_target
        while True:
            if _is_link_or_junction(curr):
                if _norm(curr) == _norm(raw_target):
                    raise PathBlocked(f"Links or junctions are never moved or changed: {p}")
                raise PathBlocked(f"Parent directory is a link or junction: {curr}")
            if _norm(curr) == _norm(root) or curr == curr.parent:
                break
            curr = curr.parent

        path = self.check_read(p)
        if _inside(path, self.data_dir) or any(_inside(path, pr) for pr in self.protected):
            raise PathBlocked("Prometheus's own data (memory, undo history) can't be changed by agents.")
        if any(_inside(pr, path) for pr in self.protected):
            raise PathBlocked(f"This folder holds Prometheus's memory, so it is not changed as a whole: {p}")
        if not _inside(path, root):
            raise PathBlocked(f"Outside the folder for this task: {p}")
        return path
