"""The owner's own repositories (M4 fix, the owner's decisions of 21 Nov 2026).

Which repos are the owner's: any repo with the same GitHub owner as ``settings.github_repo`` (``Reevias/...``),
compared ignoring case. Nothing to set, and nothing inside a repository can decide it: only the repo's name, as the
owner asked for it, against the settings.

What the owner's repos get:
- repo_check's pattern matches give "check this first" instead of "danger" (prometheus/repo_check.py ``own``);
  Microsoft Defender finding a threat still deletes it.
- A map of how they link (prometheus/repos/codemap.py ``link_repos``), shown on their Candidates cards in plain words.
- A ``NOTES.md`` next to their ``profile.json`` (What it is, Decisions, Changes), written with the same lock and
  atomic write as project notes (prometheus/projects.py ``add_notes_entry_in``). Shown as text, never as HTML.
"""
from __future__ import annotations

import time
from pathlib import Path

from prometheus.agents.github import repo_from

QUARANTINE = "quarantine"


def is_own(repo: str, owner_repo: str) -> bool:
    """True when `repo` has the same GitHub owner as `owner_repo` (the github_repo setting), ignoring case."""
    mine, theirs = repo_from(owner_repo), repo_from(repo)
    if not mine or not theirs:
        return False
    return theirs.split("/", 1)[0].casefold() == mine.split("/", 1)[0].casefold()


def _owner_repo(services) -> str:
    return str(getattr(getattr(services, "settings", None), "github_repo", "") or "")


def _quarantine() -> Path:
    from prometheus.config import data_dir

    return data_dir() / QUARANTINE


def _own_profile(services, folder: Path):
    """The profile in `folder` if it is one of the owner's repos in the quarantine, else None. The profile's own flag
    is only believed when the repo's name says so too."""
    from prometheus.repos.profile import PROFILE_NAME, load

    try:
        folder = Path(folder)
        root = _quarantine().resolve()
        if folder.is_symlink() or not folder.is_dir() or folder.resolve().parent != root:
            return None
        if (folder / PROFILE_NAME).is_symlink():
            return None
        prof = load(folder)
        if Path(prof.folder).resolve() != folder.resolve():
            return None
    except (OSError, ValueError, TypeError, KeyError):
        return None
    return prof if prof.own is True and is_own(prof.repo, _owner_repo(services)) else None


def own_profiles(services) -> list:
    """The owner's downloaded repos (newest download of each), from their profiles in <data>/quarantine."""
    root = _quarantine()
    newest: dict[str, object] = {}
    try:
        entries = sorted(root.iterdir()) if root.is_dir() else []
    except OSError:
        entries = []
    for folder in entries:
        if folder.name.startswith("."):
            continue
        prof = _own_profile(services, folder)
        if prof is None:
            continue
        key = prof.repo.casefold()
        if key not in newest or prof.made > newest[key].made:
            newest[key] = prof
    return list(newest.values())


def links(services) -> list[dict]:
    """How the owner's repos link: [{"from", "to", "why"}] from codemap.link_repos (never a stranger's repo)."""
    from prometheus.repos.codemap import link_repos

    profiles = own_profiles(services)
    if len(profiles) < 2:
        return []
    try:
        return link_repos(profiles)
    except (OSError, ValueError, KeyError, TypeError):
        return []


def card_links(repo: str, edges: list[dict]) -> list[str]:
    """The links of one repo, in plain words: "uses owner/x: imports x", "used by owner/y: ...", "shares ... with"."""
    out: list[str] = []
    me = repo.casefold()
    for e in edges:
        left, right, why = str(e.get("from", "")), str(e.get("to", "")), str(e.get("why", ""))
        if me not in (left.casefold(), right.casefold()):
            continue
        other = right if left.casefold() == me else left
        if why.startswith("shared dependency "):
            out.append(f"shares {why.removeprefix('shared dependency ')} with {other}")
        elif left.casefold() == me:
            out.append(f"uses {right}: {why}")
        else:
            out.append(f"used by {left}: {why}")
    return out


# -- NOTES.md next to profile.json -------------------------------------------------------------------------------

def _folder_for_notes(services, folder) -> Path:
    from prometheus.projects import ProjectError

    prof = _own_profile(services, Path(folder)) if folder else None
    if prof is None:
        raise ProjectError("Notes are kept only for your own downloaded repositories.")
    return Path(prof.folder)


def add_note(services, folder, section: str, text: str, first: bool = False) -> None:
    """Add one dated line to an own repo's NOTES.md (the project notes writer: a lock and an atomic write)."""
    from prometheus.projects import add_notes_entry_in

    add_notes_entry_in(_folder_for_notes(services, folder), section, text, first=first)


def start_notes(services, prof) -> None:
    """After an own repo is downloaded: its NOTES.md says what it is and what was downloaded. Only facts Prometheus
    worked out itself (never the README's words)."""
    if _own_profile(services, Path(prof.folder)) is None:
        return
    code = sorted(prof.languages, key=lambda k: -prof.languages[k])[:3]
    what = (f"{prof.repo}, one of your own repositories. Licence: {prof.licence}; "
            f"{prof.size_files} files" + (f" ({', '.join(code)})" if code else "") + ".")
    verdict = {"clean": "looks clean", "caution": "check this first"}.get(prof.verdict, prof.verdict)
    add_note(services, prof.folder, "What it is", what, first=True)
    add_note(services, prof.folder, "Changes", f"Downloaded commit {prof.commit[:7]} and checked it: {verdict} "
                                                f"({time.strftime('%H:%M')}).")


def notes_text(services, folder) -> str:
    """The NOTES.md text of an own repo, for showing as plain text ("" for anything else)."""
    from prometheus.projects import notes_file

    try:
        return notes_file({"folder": str(_folder_for_notes(services, folder))})["text"]
    except ValueError:
        return ""
