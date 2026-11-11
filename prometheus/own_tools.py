"""Tools Prometheus writes for itself (self-edit level 2).

A tool is a small Python script with a name and a description, kept in <data>/tools/<name>/. It reads its inputs
from inputs.json in its folder, prints its answer and saves any files there. The first time, the owner sees the
code on the Approvals page; once approved it runs in the sandbox (no internet, only its own folder) without asking,
until its code changes (then it needs approving again). Tools can be removed in Skills.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9_]{2,40}$")
MAX_CODE = 20_000
MAX_TOOLS = 100


class ToolError(Exception):
    pass


def digest(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


class OwnTools:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def _dir(self, name: str) -> Path:
        if not NAME.fullmatch(name or ""):
            raise ToolError("A tool name is 3 to 40 lowercase letters, digits or _, like convert_heic.")
        return self.folder / name

    def save_draft(self, name: str, description: str, code: str, inputs: list[str]) -> dict:
        code = code.strip()
        if code.startswith("```"):
            code = code.strip("`").removeprefix("python").strip()
        if not code or len(code) > MAX_CODE:
            raise ToolError("The tool's code is empty or too long.")
        d = self._dir(name)
        if not d.exists() and len(self.all()) >= MAX_TOOLS:
            raise ToolError("There are too many tools; remove some in Skills first.")
        d.mkdir(parents=True, exist_ok=True)
        (d / "tool.py").write_text(code + "\n", encoding="utf-8")
        old = self.get(name) or {}
        meta = {"name": name, "description": " ".join(description.split())[:300],
                "inputs": [str(i)[:40] for i in inputs][:10], "approved_sha": old.get("approved_sha", ""),
                "made": old.get("made", time.time()), "changed": time.time(), "runs": old.get("runs", 0)}
        (d / "tool.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        return meta

    def get(self, name: str) -> dict | None:
        try:
            meta = json.loads((self._dir(name) / "tool.json").read_text(encoding="utf-8"))
            meta["code"] = (self._dir(name) / "tool.py").read_text(encoding="utf-8")
        except (OSError, ValueError, ToolError):
            return None
        meta["approved"] = bool(meta.get("approved_sha")) and meta["approved_sha"] == digest(meta["code"])
        return meta

    def all(self) -> list[dict]:
        if not self.folder.exists():
            return []
        return [t for t in (self.get(p.name) for p in sorted(self.folder.iterdir()) if p.is_dir()) if t]

    def approve(self, name: str, sha: str) -> bool:
        """Only the exact code the owner saw is approved."""
        meta = self.get(name)
        if meta is None or digest(meta["code"]) != sha:
            return False
        path = self._dir(name) / "tool.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["approved_sha"] = sha
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")
        return True

    def count_run(self, name: str) -> None:
        path = self._dir(name) / "tool.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["runs"] = int(data.get("runs", 0)) + 1
        data["last_run"] = time.time()
        path.write_text(json.dumps(data, indent=1), encoding="utf-8")

    def remove(self, name: str) -> None:
        import shutil

        d = self._dir(name)
        if d.exists():
            shutil.rmtree(d)

    def catalog(self) -> str:
        """Approved tools, for the planner."""
        lines = [f"- {t['name']}: {t['description']} (inputs: {', '.join(t['inputs']) or 'none'})"
                 for t in self.all() if t["approved"]]
        return "\n".join(lines)
