"""Skill folders: each has a short SKILL.md and a references/ folder read only when needed.

Built-in skills ship in prometheus/skills/. The user's own skills live in <data>/skills/ and win on a name clash.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.md$")
MAX_CHARS = 20_000


@dataclass
class Skill:
    name: str
    description: str
    body: str
    folder: Path
    source: str  # "builtin" or "user"

    def references(self) -> list[str]:
        refs = self.folder / "references"
        if not refs.is_dir():
            return []
        return sorted(p.name for p in refs.iterdir() if _plain_file(p, self.folder) and REF.match(p.name))


def _plain_file(path: Path, folder: Path) -> bool:
    """A real file inside the skill folder: not a link, and not pointing anywhere else."""
    try:
        if path.is_symlink() or not path.is_file():
            return False
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:
        return False


def _parse(folder: Path, source: str) -> Skill | None:
    if not NAME.match(folder.name):
        return None
    guide = folder / "SKILL.md"
    if not _plain_file(guide, folder):
        return None
    try:
        text = guide.read_text(encoding="utf-8-sig")   # utf-8-sig: Windows tools often add a hidden marker (BOM)
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    head, sep, body = text[3:].partition("\n---")
    if not sep:
        return None
    meta = {}
    for line in head.splitlines():
        key, colon, value = line.partition(":")
        if colon:
            meta[key.strip()] = value.strip().strip('"').strip("'")
    if meta.get("name") != folder.name or not meta.get("description"):
        return None
    return Skill(folder.name, meta["description"][:300], body.lstrip("\n")[:MAX_CHARS], folder, source)


class SkillBook:
    def __init__(self, builtin: Path, user: Path | None = None):
        self.builtin = Path(builtin)
        self.user = Path(user) if user else None

    def _all(self) -> dict[str, Skill]:
        found: dict[str, Skill] = {}
        for root, source in ((self.builtin, "builtin"), (self.user, "user")):
            if root is None or not root.is_dir():
                continue
            for folder in sorted(root.iterdir()):
                refs = folder / "references"
                if folder.is_dir() and not folder.is_symlink() and not refs.is_symlink():
                    skill = _parse(folder, source)
                    if skill:
                        found[skill.name] = skill   # user skills come second, so they win
        return found

    def catalog(self) -> list[Skill]:
        return sorted(self._all().values(), key=lambda s: s.name)

    def catalog_text(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.catalog())

    def get(self, name: str) -> Skill | None:
        return self._all().get(name)

    def reference(self, name: str, ref: str) -> str:
        skill = self.get(name)
        if skill is None:
            raise ValueError(f"No such skill: {name}")
        if not REF.match(ref) or ref not in skill.references():
            raise ValueError(f"No such reference: {ref}")
        return (skill.folder / "references" / ref).read_text(encoding="utf-8-sig", errors="replace")[:MAX_CHARS]
