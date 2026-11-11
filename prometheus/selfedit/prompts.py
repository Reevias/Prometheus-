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
                "4bb8dacc80b55e2590cc594289d306f0d7b797a836540a2f65add3e564c1e9b2",
                "449a424b78bba16dd63fe18bd016e4b629c57eaced42bfe43dfee6f187546490",
                "bee3bc588912d11df58603a929b03e65621688839a21e14cb8c556fbf5548c04",
                "9c67749d9b12fe06c46bf3934c4e01295b1c70c72bd66fdced17a3cbc68a33b3",
                "f60b627e964e2eea3d189dd0ac92dcffd6ac7defd44d8f2db5cd09876f22b19f",
                "64614938dee6b44a452ab495ded2eec3c12c100a27a6c76547b5048b38f390c5",
                "b13a278536aaa94f418329fe0bb38a53b8a432c076b93bb7c50600d5d575fda8",
                "9d8a539493123a4de1d99611cfd03e293c9ce8589766ea932b09e40c0c1482ae",
                "7930c1558b2ed92e3ef7e46d58cce82d221b04a4a5f17ca6bc02bc688cce662d",
                "4685bd88d6add14f7ecfc7e4b3e1a64495456dd6899e5837775b48d0c220eade",
                "08af3931231e6dca120b836b6ac1c84415da85bec94860466d74e4d3ae9490cb",
                "24e5cdadffb9907a742dd545d9f76a086baf7b96a34859cfc7c2befc96d85ad0",
                "25c799222c7cd4e2e572f6019e15a28771e894a20db970b15cb3b3e5dff4e108",
                "93fad5559b75b61a6ea6b9cda3a72525ff086b9070db50c9b27e3db9f500f61e",
                "1fe5fd0251e1fad3cd571a93caeff3c5ec48c81f9dfd9f1be605dd1e76e2da51",
                "657bebbd7763d8b4111b799b7e04372b9b3eb70f3bb5a8333fc94616a1fded4d",
                "d58d384c8fb36e4fbe995be7964caa8c48ec7e98f8fc435bc5973726f2a065c4",
                "8ebb9cb659df404fc52246664d4b8ff628dcf32c9ceebfe436ce41d2f611ddc1",
                "36b62e78b6159f8b4673e5a352f518d395e6579822a9a539f90f823f22ed2d12",
                "c18cff9df9380094b3ddf2530900e4499103b4a9328cfef6554fd22563327ff5"},
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
