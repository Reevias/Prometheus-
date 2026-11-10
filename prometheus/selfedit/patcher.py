"""Level 2 self-editing: every change passes 6 checks and waits for the user's approval."""
from __future__ import annotations

import ast
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

LOCKED = ("safety/", "selfedit/", "orchestrator/", "server.py", "auth.py", "models/", "agents/", "actions/",
          "memory/", "web/", "skills/", "skillbook.py", "app.py", "config.py", "cli.py", "__init__.py")
EDITABLE = ("plugins/",)  # v1: Prometheus may only add or edit its own plugin files
TOOL_NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")
COMPILE_TIMEOUT = 10


@dataclass
class Proposal:
    status: Literal["queued", "skipped", "refused"]
    detail: str
    approval_id: int | None = None
    diff: str = ""


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _compiles_in_subprocess(source: str) -> str | None:
    """Return an error message, or None if the code compiles. Runs in a separate process with a time limit."""
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "candidate.py"
        f.write_text(source, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT")}
        try:
            proc = subprocess.run([sys.executable, "-I", "-m", "py_compile", str(f)], capture_output=True,
                                  text=True, timeout=COMPILE_TIMEOUT, env=env, cwd=tmp)
        except subprocess.TimeoutExpired:
            return "Check timed out."
        return proc.stderr.strip()[-500:] if proc.returncode else None


class Patcher:
    def __init__(self, code_root: Path, approvals, backups: Path):
        self.code_root = Path(code_root).resolve()
        self.approvals = approvals
        self.backups = Path(backups)

    def _target(self, rel_file: str) -> tuple[str, Path]:
        rel = rel_file.replace("\\", "/").lstrip("/")
        path = (self.code_root / rel).resolve()
        if path != self.code_root and self.code_root not in path.parents:
            raise PermissionError("Outside the Prometheus code folder.")
        rel = path.relative_to(self.code_root).as_posix()
        if any(rel == lock or rel.startswith(lock) for lock in LOCKED) or not rel.startswith(EDITABLE) \
                or rel == "plugins/__init__.py":
            raise PermissionError(f"{rel} is locked: in v1 Prometheus may only edit its own plugin files.")
        return rel, path

    def _check(self, new_src: str) -> str | None:
        try:
            ast.parse(new_src)
        except SyntaxError as e:
            return f"Syntax error: {e.msg} (line {e.lineno})"
        return _compiles_in_subprocess(new_src)

    def _queue(self, rel: str, old: str | None, new_src: str, reason: str) -> Proposal:
        diff = "".join(difflib.unified_diff((old or "").splitlines(True), new_src.splitlines(True),
                                            fromfile=f"{rel} (before)", tofile=f"{rel} (after)"))
        approval_id = self.approvals.request(
            "apply_patch", f"Self-edit {rel}: {reason}",
            {"file": rel, "new_source": new_src, "old_sha": _sha(old) if old is not None else None,
             "reason": reason, "diff": diff})
        return Proposal("queued", "Waiting for your approval.", approval_id, diff)

    def propose_insert(self, rel_file: str, anchor: str, new_code: str, reason: str) -> Proposal:
        try:
            rel, path = self._target(rel_file)
        except PermissionError as e:
            return Proposal("refused", str(e))
        if not path.is_file():
            return Proposal("refused", f"No such file: {rel}")
        src = path.read_text(encoding="utf-8")
        if new_code.strip() and new_code.strip() in src:
            return Proposal("skipped", "That change is already there.")
        count = src.count(anchor)
        if count != 1:
            return Proposal("refused", f"The insertion point must appear exactly once (found {count}).")
        i = src.index(anchor)
        line_start = src.rfind("\n", 0, i) + 1
        indent = src[line_start:i] if src[line_start:i].strip() == "" else ""
        line_end = src.find("\n", i)
        line_end = len(src) if line_end == -1 else line_end + 1
        block = "".join(indent + line + "\n" for line in new_code.strip("\n").splitlines())
        prefix = src[:line_end] if src[:line_end].endswith("\n") else src[:line_end] + "\n"
        new_src = prefix + block + src[line_end:]
        problem = self._check(new_src)
        if problem:
            return Proposal("refused", problem)
        return self._queue(rel, src, new_src, reason)

    def propose_new_tool(self, name: str, content: str, reason: str) -> Proposal:
        if not TOOL_NAME.match(name):
            return Proposal("refused", "Tool names use lowercase letters, digits and _ only.")
        rel, path = self._target(f"plugins/{name}.py")
        if path.exists():
            return Proposal("refused", f"{rel} already exists.")
        problem = self._check(content)
        if problem:
            return Proposal("refused", problem)
        return self._queue(rel, None, content, reason)

    def apply(self, payload: dict) -> str:
        rel, path = self._target(payload["file"])
        old_sha = payload.get("old_sha")
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if (old_sha is None and current is not None) or (old_sha is not None and
                                                         (current is None or _sha(current) != old_sha)):
            return f"Not applied: {rel} changed since the proposal. Ask Prometheus to propose it again."
        backup = None
        if current is not None:
            self.backups.mkdir(parents=True, exist_ok=True)
            backup = self.backups / f"{rel.replace('/', '__')}.{time.strftime('%Y%m%d-%H%M%S')}.bak"
            shutil.copy2(path, backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload["new_source"], encoding="utf-8")
        try:
            ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            if backup:
                shutil.copy2(backup, path)
            else:
                path.unlink()
            return f"Not applied: {rel} failed the final check and was restored."
        return f"Applied to {rel}." + (f" Backup: {backup.name}" if backup else "")
