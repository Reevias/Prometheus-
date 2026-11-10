"""Move, rename and recycle files safely. Never overwrites, never deletes."""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from send2trash import send2trash

from prometheus.actions.journal import Journal
from prometheus.safety.paths import PathBlocked, PathPolicy

MTIME_TOLERANCE = 0.01


@dataclass
class FileStamp:
    mtime: float
    size: int


def stamp(p: Path) -> FileStamp:
    st = Path(p).stat()
    return FileStamp(st.st_mtime, st.st_size)


@dataclass
class ActionResult:
    status: Literal["done", "skipped", "failed"]
    detail: str
    new_path: Path | None = None


@dataclass
class UndoReport:
    restored: int = 0
    problems: list[str] = field(default_factory=list)


def _unique(target: Path) -> Path:
    if not target.exists():
        return target
    n = 1
    while True:
        candidate = target.with_name(f"{target.stem} ({n}){target.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


class FileActions:
    def __init__(self, policy: PathPolicy, journal: Journal, trash: Callable[[str], None] = send2trash):
        self.policy = policy
        self.journal = journal
        self.trash = trash

    # -- checks --------------------------------------------------------------
    def _precheck(self, src: Path, root: Path, expected: FileStamp | None) -> tuple[Path, ActionResult | None]:
        try:
            path = self.policy.check_change(src, root)
        except PathBlocked as e:
            return Path(src), ActionResult("skipped", str(e))
        if not path.exists():
            return path, ActionResult("skipped", "No longer exists.")
        if expected is not None:
            now = stamp(path)
            if abs(now.mtime - expected.mtime) > MTIME_TOLERANCE or now.size != expected.size:
                return path, ActionResult("skipped", "Changed since the scan. Scan again first.")
        return path, None

    def _make_dirs(self, run_id: str, root: Path, target_dir: Path) -> None:
        missing = []
        cur = target_dir
        while cur != root and not cur.exists():
            missing.append(cur)
            cur = cur.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        self.journal.record_dirs(run_id, [str(d.relative_to(root)) for d in reversed(missing)])

    def _relocate(self, run_id: str, op: str, path: Path, target: Path, root: Path) -> ActionResult:
        try:
            self._make_dirs(run_id, root, target.parent)
            target = _unique(target)
            shutil.move(str(path), str(target))
        except OSError as e:
            return ActionResult("skipped", f"File in use or not allowed: {e.strerror or e}")
        self.journal.record(run_id, {"op": op, "from": str(path.relative_to(root)),
                                     "to": str(target.relative_to(root)), "undone": False})
        return ActionResult("done", "Moved." if op == "move" else "Renamed.", target)

    # -- actions -------------------------------------------------------------
    def move(self, run_id: str, src: Path, dest_dir: Path, root: Path,
             expected: FileStamp | None = None) -> ActionResult:
        root = Path(root).resolve()
        path, problem = self._precheck(src, root, expected)
        if problem:
            return problem
        try:
            target_dir = self.policy.check_change(dest_dir, root)
        except PathBlocked as e:
            return ActionResult("skipped", str(e))
        if path.parent == target_dir:
            return ActionResult("skipped", "Already in that folder.")
        if path.is_dir() and (target_dir == path or path in target_dir.parents):
            return ActionResult("skipped", "Can't move a folder into itself.")
        return self._relocate(run_id, "move", path, target_dir / path.name, root)

    def rename(self, run_id: str, src: Path, new_name: str, root: Path,
               expected: FileStamp | None = None) -> ActionResult:
        root = Path(root).resolve()
        if not new_name or "/" in new_name or "\\" in new_name or new_name in (".", ".."):
            return ActionResult("skipped", "Invalid new name.")
        path, problem = self._precheck(src, root, expected)
        if problem:
            return problem
        return self._relocate(run_id, "rename", path, path.with_name(new_name), root)

    def copy(self, run_id: str, src: Path, dest_dir: Path, root: Path) -> ActionResult:
        """Copy a file or folder into dest_dir (inside root). The source may be anywhere Prometheus may read.
        Never overwrites: a clash gets a "(1)" name. Undo sends the copy to the Recycle Bin."""
        root = Path(root).resolve()
        try:
            source = self.policy.check_read(src)
            if Path(src).is_symlink():
                raise PathBlocked(f"Links are never copied: {src}")
            target_dir = self.policy.check_change(dest_dir, root)
        except PathBlocked as e:
            return ActionResult("skipped", str(e))
        if not source.exists():
            return ActionResult("skipped", "No longer exists.")
        if source.is_dir() and (target_dir == source or source in target_dir.parents):
            return ActionResult("skipped", "Can't copy a folder into itself.")
        try:
            self._make_dirs(run_id, root, target_dir)
            target = _unique(target_dir / source.name)
            if source.is_dir():
                shutil.copytree(source, target, symlinks=True)
            else:
                shutil.copy2(source, target)
        except OSError as e:
            return ActionResult("skipped", f"Could not copy: {e.strerror or e}")
        self.journal.record(run_id, {"op": "copy", "source": str(source), "to": str(target.relative_to(root)),
                                     "undone": False})
        return ActionResult("done", "Copied.", target)

    def make_folder(self, run_id: str, folder: Path, root: Path) -> ActionResult:
        root = Path(root).resolve()
        try:
            target = self.policy.check_change(folder, root)
        except PathBlocked as e:
            return ActionResult("skipped", str(e))
        if target.exists():
            return ActionResult("skipped", "That folder already exists.", target)
        self._make_dirs(run_id, root, target)
        return ActionResult("done", "Folder made.", target)

    def recycle(self, run_id: str, src: Path, root: Path, expected: FileStamp | None = None) -> ActionResult:
        root = Path(root).resolve()
        path, problem = self._precheck(src, root, expected)
        if problem:
            return problem
        try:
            self.trash(str(path))
        except OSError as e:
            return ActionResult("skipped", f"File in use or not allowed: {e.strerror or e}")
        self.journal.record(run_id, {"op": "recycle", "from": str(path.relative_to(root)), "undone": False})
        return ActionResult("done", "Moved to the Recycle Bin.")

    # -- undo ----------------------------------------------------------------
    def _undo_one(self, root: Path, entry: dict, report: UndoReport) -> None:
        if entry.get("undone"):
            return
        if entry["op"] == "recycle":
            report.problems.append(f"{entry['from']}: in Recycle Bin (restore it from there)")
            return
        if entry["op"] == "copy":       # undoing a copy: the copy goes to the Recycle Bin, the original stays
            copy = (root / entry["to"]).resolve()
            try:
                self.policy.check_change(copy, root)
                if copy.exists():
                    self.trash(str(copy))
            except (PathBlocked, OSError) as e:
                report.problems.append(f"{entry['to']}: {e}")
                return
            entry["undone"] = True
            report.restored += 1
            return
        current, original = (root / entry["to"]).resolve(), (root / entry["from"]).resolve()
        try:
            self.policy.check_change(current, root)
            self.policy.check_change(original, root)
        except PathBlocked as e:
            report.problems.append(f"{entry['from']}: {e}")
            return
        if not current.exists():
            report.problems.append(f"{entry['to']}: no longer there")
            return
        if original.exists():
            report.problems.append(f"{entry['from']}: something else is there now")
            return
        try:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(current), str(original))
        except OSError as e:
            report.problems.append(f"{entry['to']}: {e.strerror or e}")
            return
        entry["undone"] = True
        report.restored += 1

    def undo_run(self, run_id: str) -> UndoReport:
        if run_id in self.journal.active:
            return UndoReport(problems=["This run is still running. Wait for it to finish, then undo."])
        with self.journal.lock:
            data = self.journal.load(run_id)
            root = Path(data["root"])
            report = UndoReport()
            for entry in reversed(data["entries"]):
                self._undo_one(root, entry, report)
            for folder in reversed(data["created_dirs"]):
                try:
                    (root / folder).rmdir()
                except OSError:
                    pass
            pending = [e for e in data["entries"] if e["op"] != "recycle" and not e.get("undone")]
            data["last_undo"] = {"restored": report.restored, "problems": report.problems}
            if not pending:  # only mark undone when every move was put back; otherwise allow a retry
                data["undone"] = data["last_undo"]
            self.journal.update(run_id, data)
            return report

    def undo_entry(self, run_id: str, index: int) -> UndoReport:
        if run_id in self.journal.active:
            return UndoReport(problems=["This run is still running. Wait for it to finish, then undo."])
        with self.journal.lock:
            data = self.journal.load(run_id)
            report = UndoReport()
            self._undo_one(Path(data["root"]), data["entries"][index], report)
            self.journal.update(run_id, data)
            return report
