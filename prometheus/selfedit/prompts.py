"""Level 1 self-editing: prompt files in a local git repo, so every change can be rolled back."""
from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
from pathlib import Path

PACKAGE_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
# sha256 of prompt files shipped by earlier versions. A copy that still matches one of these was never edited by
# the owner or by self-editing, so it is replaced when a better version ships.
KNOWN_OLD: dict[str, set[str]] = {
    "planner": {"0c1af9132202917489d1136e99c9fd7e383961b835324aa1dac5f3af4a10f211",
                "139ffcfc2f2e4ee0702e1930b9406bd7e36106abad08bff9653d8f9708a5e449",
                "30d718d09ad22bd4132fece5371a35593a5c4751be8318d7cafd10a86c33c2b5",
                "4bb8dacc80b55e2590cc594289d306f0d7b797a836540a2f65add3e564c1e9b2"},
    "combine": {"24165848dd711163a1f1400ad502d25d6217a5496062d8462a981ab801bf935d"},
}
NAME = re.compile(r"^[a-z_][a-z0-9_]{0,40}$")


class PromptStore:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        for f in PACKAGE_PROMPTS.glob("*.md"):
            if not (self.folder / f.name).exists():
                shutil.copy2(f, self.folder / f.name)
        self._git_ok = shutil.which("git") is not None
        if self._git_ok and not (self.folder / ".git").exists():
            self._git("init", "-q")
            self._git("add", "-A")
            self._git("commit", "-q", "-m", "Initial prompts", "--allow-empty")
        for shipped in PACKAGE_PROMPTS.glob("*.md"):
            current = self.folder / shipped.name
            digest = hashlib.sha256(current.read_bytes()).hexdigest()
            new_text = shipped.read_text(encoding="utf-8")
            if digest in KNOWN_OLD.get(shipped.stem, set()) and current.read_text(encoding="utf-8") != new_text:
                try:
                    self.set(shipped.stem, new_text, "Update to the improved shipped prompt")
                except RuntimeError:      # git trouble must never stop Prometheus from starting
                    current.write_text(new_text, encoding="utf-8")

    def _git(self, *args: str) -> str:
        proc = subprocess.run(["git", "-c", "user.name=Prometheus", "-c", "user.email=prometheus@localhost",
                               "-c", "commit.gpgsign=false", *args], cwd=self.folder, capture_output=True,
                              text=True, timeout=30)
        if proc.returncode:
            raise RuntimeError(proc.stderr.strip()[:300])
        return proc.stdout

    def _path(self, name: str) -> Path:
        if not NAME.match(name):
            raise ValueError(f"Invalid prompt name: {name}")
        return self.folder / f"{name}.md"

    def get(self, name: str) -> str:
        return self._path(name).read_text(encoding="utf-8")

    def set(self, name: str, text: str, reason: str) -> None:
        path = self._path(name)
        path.write_text(text, encoding="utf-8")
        if self._git_ok:
            self._git("add", path.name)
            self._git("commit", "-q", "-m", reason or f"Update {name}")

    def history(self, name: str) -> list[tuple[str, str]]:
        if not self._git_ok:
            return []
        out = self._git("log", "--format=%H%x09%s", "--", self._path(name).name)
        return [tuple(line.split("\t", 1)) for line in out.splitlines() if "\t" in line]

    def rollback(self, name: str, commit: str) -> None:
        if not re.fullmatch(r"[0-9a-f]{7,40}", commit):
            raise ValueError("Invalid version id")
        old = self._git("show", f"{commit}:{self._path(name).name}")
        self.set(name, old, f"Roll back {name} to {commit[:7]}")
