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


class PathBlocked(Exception):
    """Raised when a path is outside what Prometheus may touch."""


def _norm(p: Path) -> str:
    return os.path.normcase(str(p))


def _inside(child: Path, parent: Path) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip(os.sep) + os.sep)


class PathPolicy:
    def __init__(self, home: Path, data_dir: Path, code_dir: Path):
        self.home = Path(home).resolve()
        self.data_dir = Path(data_dir).resolve()
        self.code_dir = Path(code_dir).resolve()
        self._blocked = [self.home / rel for rel in BLOCKED_UNDER_HOME] + [self.code_dir]

    def _resolve(self, p: Path | str) -> Path:
        return Path(os.path.abspath(Path(p).expanduser())).resolve()

    def is_blocked(self, p: Path | str) -> bool:
        path = self._resolve(p)
        if not _inside(path, self.home):
            return True
        if _inside(path, self.data_dir):
            name = os.path.normcase(path.name).lower()   # keys, password hash and their temp/backup copies
            return path.parent == self.data_dir and (name.startswith(".env") or name.startswith("settings."))
        if any(_inside(path, b) for b in self._blocked):
            return True
        rel_parts = path.relative_to(self.home).parts if _norm(path) != _norm(self.home) else ()
        return any(os.path.normcase(part).lower().startswith(BLOCKED_NAME_PREFIXES) for part in rel_parts)

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
        if _inside(path, self.data_dir):
            raise PathBlocked("Prometheus's own data (memory, undo history) can't be changed by agents.")
        if not _inside(path, root):
            raise PathBlocked(f"Outside the folder for this task: {p}")
        return path
