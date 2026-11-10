"""Which files Prometheus may read or change."""
from __future__ import annotations

import os
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


# Never usable, even inside an extra folder the owner added (for example a whole drive).
SYSTEM_DIRS = tuple(p for p in (
    os.environ.get("SystemRoot", r"C:\Windows"), os.environ.get("ProgramFiles", r"C:\Program Files"),
    os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramData", r"C:\ProgramData"),
    r"C:\$Recycle.Bin", r"C:\System Volume Information", "/etc", "/bin", "/sbin", "/usr", "/boot", "/proc", "/sys",
    "/dev", "/System", "/Library") if p)


def extra_root_problem(folder: str, home: Path) -> str:
    """Why a folder can't be added as an extra place, or '' if it can."""
    raw = (folder or "").strip().strip('"')
    if not raw:
        return "Type a folder or drive, for example D:\\ or D:\\Photos."
    path = Path(os.path.abspath(Path(raw).expanduser()))
    if not path.is_absolute() or not path.exists() or not path.is_dir():
        return f"{raw} doesn't exist or isn't a folder."
    resolved = path.resolve()
    system_drive = os.environ.get("SystemDrive", "C:").rstrip("\\/") + os.sep
    if _norm(resolved) in (_norm(Path(system_drive)), _norm(Path("/"))):
        return "The whole system drive can't be added. Add a folder on it instead, or another drive."
    for sys_dir in SYSTEM_DIRS:
        if _inside(resolved, Path(sys_dir)) or _inside(Path(sys_dir), resolved):
            return f"{resolved} holds system files, so it can't be added."
    if _inside(resolved, Path(home).resolve()):
        return "That is already inside your user folder, which Prometheus can use."
    return ""


class PathBlocked(Exception):
    """Raised when a path is outside what Prometheus may touch."""


def _norm(p: Path) -> str:
    return os.path.normcase(str(p))


def _inside(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


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
        return Path(os.path.abspath(Path(p).expanduser())).resolve()

    def is_blocked(self, p: Path | str) -> bool:
        path = self._resolve(p)
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
        path = self._resolve(p)
        best = None
        for r in self.roots():
            if _inside(path, r) and (best is None or len(str(r)) > len(str(best))):
                best = r
        return best

    def check_read(self, p: Path | str) -> Path:
        path = self._resolve(p)
        if self.is_blocked(path):
            raise PathBlocked(f"Not allowed: {p}")
        return path

    def check_change(self, p: Path | str, task_root: Path) -> Path:
        if Path(p).is_symlink():
            raise PathBlocked(f"Links are never moved or changed: {p}")
        root = self._resolve(task_root)
        if self.is_blocked(root):
            raise PathBlocked(f"Not allowed to change files in: {task_root}")
        path = self.check_read(p)
        if _inside(path, self.data_dir) or any(_inside(path, p) for p in self.protected):
            raise PathBlocked("Prometheus's own data (memory, undo history) can't be changed by agents.")
        if any(_inside(p, path) for p in self.protected):
            raise PathBlocked(f"This folder holds Prometheus's memory, so it is not changed as a whole: {p}")
        if not _inside(path, root):
            raise PathBlocked(f"Outside the folder for this task: {p}")
        return path
