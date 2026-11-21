"""Skill folders: each has a short SKILL.md and a references/ folder read only when needed.

Built-in skills ship in prometheus/skills/. The user's own skills live in <data>/skills/ and win on a name clash.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

NAME = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,80}\.md$")
MAX_CHARS = 20_000
MAX_MANIFEST = 200_000      # bytes; manifest.json format: docs/m4/MANIFEST.md


@dataclass
class Skill:
    name: str
    description: str
    body: str
    folder: Path
    source: str  # "builtin" or "user"
    meta: dict | None = None      # the other SKILL.md header lines (version, author, requires, permissions, category…)
    truncated: bool = False       # the guide was longer than MAX_CHARS and was cut
    manifest: dict | None = None  # manifest.json next to SKILL.md (M4.4), if there is a valid one

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


def _manifest(folder: Path, name: str = "") -> dict | None:
    """manifest.json as a dict, only if it is a plain file inside the folder, readable JSON, and names this skill.
    Anything else is ignored: the skill then loads exactly as one without a manifest."""
    path = folder / "manifest.json"
    if not _plain_file(path, folder):
        return None
    try:
        if path.stat().st_size > MAX_MANIFEST:
            return None
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("name") == (name or folder.name) else None


def _parse(folder: Path, source: str, name: str = "") -> Skill | None:
    """name: for a library skill cached as <name>@<version>/ (M4.5); otherwise the folder's own name."""
    name = name or folder.name
    if not NAME.match(name):
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
    if meta.get("name") != name or not meta.get("description"):
        return None
    body = body.lstrip("\n")
    extra = {k: v[:200] for k, v in meta.items() if k not in ("name", "description")}
    return Skill(name, meta["description"][:300], body[:MAX_CHARS], folder, source, extra, len(body) > MAX_CHARS,
                 _manifest(folder, name))


class SkillBook:
    def __init__(self, builtin: Path, user: Path | None = None):
        self.builtin = Path(builtin)
        self.user = Path(user) if user else None
        self.is_off = lambda name: False     # set by the app: the owner's switches on the Skills page
        self.library = None                  # the owner's skills library on GitHub (M4.5), set by the app

    def _all(self) -> dict[str, Skill]:
        found: dict[str, Skill] = {}
        for e in (self.library.entries() if self.library is not None else []):
            # listed from the saved index only: nothing is downloaded until the skill is used (get)
            found[e["name"]] = Skill(e["name"], e["description"], "", self.library.cache / f"{e['name']}@{e['version']}",
                                     "library", {"version": e["version"]})
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
        """A skill to use now. A library skill is downloaded and checked the first time (library.load); if that
        can't happen, LibraryError says why in plain words."""
        skill = self._all().get(name)
        if skill is None or self.is_off(skill.name):
            return None
        if skill.source == "library":
            from prometheus.repos.library import LibraryError

            loaded = _parse(self.library.load(name), "library", name)
            if loaded is None:
                raise LibraryError(f"/{name} from your skills library has no valid SKILL.md.")
            return loaded
        return skill

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
