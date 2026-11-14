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
    meta: dict | None = None      # the other SKILL.md header lines (version, author, requires, permissions, category…)
    truncated: bool = False       # the guide was longer than MAX_CHARS and was cut

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
    body = body.lstrip("\n")
    extra = {k: v[:200] for k, v in meta.items() if k not in ("name", "description")}
    return Skill(folder.name, meta["description"][:300], body[:MAX_CHARS], folder, source, extra, len(body) > MAX_CHARS)


class SkillBook:
    def __init__(self, builtin: Path, user: Path | None = None):
        self.builtin = Path(builtin)
        self.user = Path(user) if user else None
        self.is_off = lambda name: False     # set by the app: the owner's switches on the Skills page

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

    def everything(self) -> list[Skill]:
        """Every skill, switched off or not (the Skills page)."""
        return sorted(self._all().values(), key=lambda s: s.name)

    def catalog(self) -> list[Skill]:
        """The skills the planner may use: switched-off ones are left out."""
        return [s for s in self.everything() if not self.is_off(s.name)]

    def catalog_text(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.catalog())

    def get(self, name: str) -> Skill | None:
        skill = self._all().get(name)
        return None if skill is None or self.is_off(skill.name) else skill

    def get_any(self, name: str) -> Skill | None:
        """A skill even if it's switched off (to show it, or switch it back on)."""
        return self._all().get(name)

    def reference(self, name: str, ref: str) -> str:
        skill = self.get_any(name)
        if skill is None:
            raise ValueError(f"No such skill: {name}")
        if not REF.match(ref) or ref not in skill.references():
            raise ValueError(f"No such reference: {ref}")
        return (skill.folder / "references" / ref).read_text(encoding="utf-8-sig", errors="replace")[:MAX_CHARS]


def check(skill: Skill) -> list[tuple[bool, str]]:
    """A quick test-run of a skill's files, without any AI: what Prometheus will be able to read, and any problem."""
    out = [(True, "The guide (SKILL.md) has a name and a description, so the planner can find it.")]
    if len(skill.description) < 20:
        out.append((False, "The description is very short: the planner may not know when to use it."))
    if not skill.body.strip():
        out.append((False, "The guide has no text after its header."))
    elif skill.truncated:
        out.append((False, f"The guide is longer than {MAX_CHARS:,} characters: only the start is read."))
    else:
        out.append((True, f"The guide is {len(skill.body):,} characters: all of it is read."))
    refs = skill.references()
    for name in refs:
        try:
            size = (skill.folder / "references" / name).stat().st_size
            (skill.folder / "references" / name).read_text(encoding="utf-8-sig")
            note = "" if size <= MAX_CHARS else f" (only the first {MAX_CHARS:,} characters are read)"
            out.append((size <= MAX_CHARS, f"Reference {name} can be read{note}."))
        except (OSError, UnicodeDecodeError):
            out.append((False, f"Reference {name} can't be read as text."))
    folder = skill.folder / "references"
    if folder.is_dir():
        skipped = [p.name for p in folder.iterdir() if p.name not in refs]
        if skipped:
            out.append((False, "Ignored (not plain .md files inside the skill): " + ", ".join(sorted(skipped)[:8])))
    if not refs:
        out.append((True, "No reference files: the guide alone is used."))
    return out
