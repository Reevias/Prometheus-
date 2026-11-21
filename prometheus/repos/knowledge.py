"""Repo knowledge for search (M4.7): the useful parts of an ACCEPTED repo become notes in the existing search index.

What goes in (never the whole repo, at most 200 notes per repo, in this order):
  1. the README, one note per heading section;
  2. docstrings of public Python functions and classes the code map lists (read with `ast`, nothing is run);
  3. example files (examples/, example/, samples/, docs/).
Every note is saved with the source "from owner/name at abc1234" and its text wrapped by `untrusted(text, source)`:
it is the repository's words, information only, never instructions. Notes are private (kept on the laptop, never sent
to a cloud model) and are searched with `search()`. Nothing in the repo is imported, executed or installed.
"""
from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from prometheus.agents.base import untrusted
from prometheus.repos.profile import MAX_READ, RepoProfile, _read

MAX_NOTES = 200
NOTE_CHARS = 3000
MIN_CHARS = 20                                    # a heading with nothing under it is not worth a note
EXAMPLE_DIRS = ("examples", "example", "samples", "sample", "docs")
EXAMPLE_SUFFIXES = {".py", ".js", ".mjs", ".ts", ".md", ".rst", ".txt", ".sh", ".ps1", ".go", ".rs", ".java", ".cs"}
EXAMPLE_FILES_SEEN = 400                          # how many files in those folders are looked at
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def source_of(profile: RepoProfile) -> str:
    return f"from {profile.repo} at {profile.commit[:7]}"


def _key(repo: str, n: int) -> str:
    return f"repo:{repo}:{n}"


def _note(profile: RepoProfile, title: str, body: str) -> dict | None:
    body = body.strip()[:NOTE_CHARS]
    if len(body) < MIN_CHARS:
        return None
    src = source_of(profile)
    return {"title": f"{profile.repo}: {title}"[:200], "source": src,
            "text": untrusted(f"{title}\n\n{body}", f"{src}: {title}"[:200])}


def _readme_notes(profile: RepoProfile, folder: Path):
    for path in sorted(folder.iterdir()):
        if path.is_file() and path.name.lower().split(".")[0] == "readme":
            text = _read(path, MAX_READ).replace("\r\n", "\n")
            break
    else:
        return
    heading, lines, fenced = "README", [], False
    sections = []
    for line in text.split("\n"):
        if FENCE.match(line):
            fenced = not fenced
        m = None if fenced else HEADING.match(line)
        if m:
            sections.append((heading, "\n".join(lines)))
            heading, lines = m.group(2), []
        else:
            lines.append(line)
    sections.append((heading, "\n".join(lines)))
    for title, body in sections:
        note = _note(profile, f"README, {title}", body)
        if note:
            yield note


def _load_codemap(folder: Path, codemap):
    if isinstance(codemap, dict):
        return codemap
    path = folder / "codemap.json"
    try:
        if path.is_file() and not path.is_symlink() and path.stat().st_size <= 5_000_000:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        pass
    return None


def _docstring_notes(profile: RepoProfile, folder: Path, codemap: dict):
    """Docstrings of public Python functions and classes that the code map lists. Other languages: names only in the
    code map, so no docstrings are taken from them."""
    root = folder.resolve()
    for item in codemap.get("files", []):
        if not isinstance(item, dict) or item.get("language") != "Python":
            continue
        wanted = {s.get("name") for s in item.get("functions", []) if isinstance(s, dict)}
        for cls in item.get("classes", []):
            if isinstance(cls, dict):
                wanted.add(cls.get("name"))
                wanted.update(m.get("name") for m in cls.get("methods", []) if isinstance(m, dict))
        wanted = {n for n in wanted if isinstance(n, str) and not n.startswith("_")}
        rel = str(item.get("path", ""))
        path = folder / rel
        try:
            if not wanted or not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file() \
                    or path.stat().st_size > MAX_READ:
                continue
            tree = ast.parse(path.read_bytes())
        except (OSError, ValueError, SyntaxError, RecursionError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name in wanted:
                doc = ast.get_docstring(node)
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                note = _note(profile, f"{kind} {node.name} in {Path(rel).as_posix()}", doc or "")
                if note:
                    yield note


def _example_notes(profile: RepoProfile, folder: Path):
    root, seen = folder.resolve(), 0
    for name in EXAMPLE_DIRS:
        base = folder / name
        if not base.is_dir() or base.is_symlink():
            continue
        for path in sorted(base.rglob("*")):
            seen += 1
            if seen > EXAMPLE_FILES_SEEN:
                return
            try:
                if path.suffix.lower() not in EXAMPLE_SUFFIXES or path.is_symlink() or not path.is_file() \
                        or not path.resolve().is_relative_to(root) or path.name.startswith("."):
                    continue
            except OSError:
                continue
            rel = path.relative_to(folder).as_posix()
            note = _note(profile, f"example {rel}", _read(path, NOTE_CHARS * 4))
            if note:
                yield note


def collect_notes(profile: RepoProfile, codemap: dict | None = None) -> list[dict]:
    """The notes for a repo ({title, source, text}), at most MAX_NOTES. Reads files; saves nothing."""
    folder = Path(profile.folder)
    if not folder.is_dir():
        return []
    notes: list[dict] = []
    cmap = _load_codemap(folder, codemap)
    streams = [_readme_notes(profile, folder)]
    if cmap:
        streams.append(_docstring_notes(profile, folder, cmap))
    streams.append(_example_notes(profile, folder))
    for stream in streams:
        for note in stream:
            notes.append(note)
            if len(notes) >= MAX_NOTES:
                return notes
    return notes


def accepted(store, repo: str) -> bool:
    from prometheus.repo_of_day import candidates

    return any(c["repo"].lower() == repo.lower() and c["status"] == "accepted" for c in candidates(store))


def forget_repo(store, repo: str) -> int:
    """Remove every note of `repo`. Returns how many there were."""
    keys = store.document_keys(f"repo:{repo}:")
    for doc_id in keys.values():
        store.delete_document(doc_id)
    return len(keys)


def index_repo(store, profile: RepoProfile, codemap: dict | None = None) -> int:
    """Add the useful parts of an ACCEPTED repo to the search index. A repo that is new, rejected, unknown or unsafe is
    never indexed (returns 0). Indexing again replaces the old notes. Returns the number of notes saved."""
    if profile.verdict == "danger" or not accepted(store, profile.repo):
        return 0
    notes = collect_notes(profile, codemap)
    forget_repo(store, profile.repo)
    for n, note in enumerate(notes):
        store.add_document(_key(profile.repo, n), note["title"], note["source"], note["text"], private=True)
    return len(notes)


def search(store, query: str, limit: int = 8) -> list[dict]:
    """Repo notes that match, best first: {repo, title, source ("from owner/name at abc1234"), passage}."""
    out = []
    for hit in store.search_chunks(query, limit=limit * 3, include_private=True, chats=False):
        if not str(hit.source).startswith("from ") or " at " not in str(hit.source):
            continue
        rows = store._rows("SELECT key FROM documents WHERE id = ?", (hit.doc_id,))
        if not rows or not str(rows[0]["key"]).startswith("repo:"):
            continue
        out.append({"repo": str(rows[0]["key"]).split(":", 1)[1].rsplit(":", 1)[0], "title": hit.title,
                    "source": hit.source, "passage": " ".join(hit.match.split())[:300]})
        if len(out) >= limit:
            break
    return out


def sync_candidate(store, candidate: dict, quarantine: Path) -> int:
    """Called when a candidate is accepted (index it) or rejected/removed (forget it). Never raises: a repo that can't
    be read just gets no notes."""
    try:
        if candidate.get("status") != "accepted":
            return forget_repo(store, candidate["repo"])
        from prometheus.repos.profile import load

        folder = Path(candidate.get("folder") or "")
        if not candidate.get("folder") or not folder.resolve().is_relative_to(Path(quarantine).resolve()):
            return 0
        return index_repo(store, load(folder))
    except Exception:
        try:
            store.audit("system", "repo_knowledge_failed", {"repo": str(candidate.get("repo", ""))[:100]})
        except Exception:
            pass
        return 0
