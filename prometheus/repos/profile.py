"""The repo profile: what Prometheus knows about a downloaded repository, without running any of it.

Made by M4.1 (`make_profile`), read by M4.2 (code maps), M4.3 (Candidates tab), M4.4 (skill builder) and M4.7.
Saved as ``profile.json`` (UTF-8 JSON object) inside the repo's quarantine folder,
``<data>/quarantine/<owner>__<name>__<sha7>/profile.json``. Exactly these keys, nothing else:

=================  ===================  ==============================================================================
key                type                 meaning
=================  ===================  ==============================================================================
``repo``           str                  ``owner/name``
``commit``         str                  the 40-character commit that was downloaded (pinned)
``url``            str                  ``https://github.com/owner/name``
``folder``         str                  where it was unpacked (absolute path inside ``<data>/quarantine/``)
``verdict``        str                  ``clean``, ``caution`` or ``danger`` (from prometheus/repo_check.py)
``findings``       list[str]            repo_check's reasons in plain words, ``"<what>: <file>"``
``licence``        str                  SPDX id (``MIT``, ``Apache-2.0``, ``GPL-3.0-only``…) or ``unknown``
``languages``      dict[str, int]       language → number of files (by file extension; files with no known
                                        extension are not counted; data formats such as JSON/TOML/Markdown count)
``size_files``     int                  number of files (``profile.json`` itself not counted)
``size_bytes``     int                  their total size in bytes
``entry_points``   list[str]            CLI scripts as ``"name = target"`` (pyproject ``[project.scripts]``, setup.cfg
                                        ``console_scripts``, package.json ``bin``), then files: package.json ``main``,
                                        any ``__main__.py``, top-level ``main.py``/``app.py``/``cli.py``/``manage.py``,
                                        ``index.js``, ``src/main.rs``, ``main.go``/``cmd/*/main.go`` (relative paths,
                                        ``/`` separators)
``dependencies``   list[dict]           ``{"name", "version", "source"}``; version is the constraint as written
                                        (``""`` when none); source is the file (``package.json (dev)`` for
                                        devDependencies). Read from pyproject.toml, requirements*.txt, setup.cfg,
                                        package.json, go.mod, Cargo.toml in that order, as text (never executed).
``readme_summary`` str                  first 1500 characters of the top-level README, wrapped by
                                        ``untrusted(text, "README of owner/name")``; ``""`` when there is none.
                                        It is data, never instructions.
``own``            bool                 true when the repo belongs to the owner
``made``           float                ``time.time()`` when the profile was made
=================  ===================  ==============================================================================

Every file is read with a size cap (1 MB) as UTF-8 text; nothing in the repository is imported, executed or installed.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import time
import tomllib
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from prometheus.agents.base import untrusted

PROFILE_NAME = "profile.json"
README_CHARS = 1500
MAX_READ = 1_000_000            # bytes read from any one metadata file

LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".js": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".kt": "Kotlin", ".cs": "C#", ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".hpp": "C++", ".rb": "Ruby",
    ".php": "PHP", ".swift": "Swift", ".lua": "Lua", ".r": "R", ".jl": "Julia", ".dart": "Dart", ".scala": "Scala",
    ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell", ".psm1": "PowerShell", ".bat": "Batch", ".cmd": "Batch",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "CSS", ".vue": "Vue", ".svelte": "Svelte",
    ".ipynb": "Jupyter Notebook", ".sql": "SQL", ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Text",
    ".json": "JSON", ".toml": "TOML", ".yml": "YAML", ".yaml": "YAML", ".cfg": "INI", ".ini": "INI", ".xml": "XML",
}

# Distinctive phrases from each licence's own text → SPDX id. Checked in order (more specific first).
LICENCE_TEXT = [
    ("AGPL-3.0-only", (r"GNU AFFERO GENERAL PUBLIC LICENSE", r"Version 3")),
    ("LGPL-3.0-only", (r"GNU LESSER GENERAL PUBLIC LICENSE", r"Version 3")),
    ("LGPL-2.1-only", (r"GNU LESSER GENERAL PUBLIC LICENSE", r"Version 2\.1")),
    ("GPL-3.0-only", (r"GNU GENERAL PUBLIC LICENSE", r"Version 3")),
    ("GPL-2.0-only", (r"GNU GENERAL PUBLIC LICENSE", r"Version 2")),
    ("Apache-2.0", (r"Apache License", r"Version 2\.0")),
    ("MPL-2.0", (r"Mozilla Public License", r"2\.0")),
    ("MIT", (r"Permission is hereby granted, free of charge",)),
    ("BSD-3-Clause", (r"Redistribution and use in source and binary forms", r"Neither the name")),
    ("BSD-2-Clause", (r"Redistribution and use in source and binary forms",)),
    ("ISC", (r"Permission to use, copy, modify, and(/or)? distribute this software for any purpose",)),
    ("Unlicense", (r"This is free and unencumbered software released into the public domain",)),
    ("CC0-1.0", (r"CC0 1\.0 Universal",)),
]
ALIASES = {"mit license": "MIT", "the mit license": "MIT", "apache 2.0": "Apache-2.0", "apache-2": "Apache-2.0",
           "apache license 2.0": "Apache-2.0", "apache software license": "Apache-2.0", "bsd": "BSD-3-Clause",
           "gpl-3.0": "GPL-3.0-only", "gplv3": "GPL-3.0-only", "gpl-2.0": "GPL-2.0-only", "gplv2": "GPL-2.0-only",
           "lgpl-3.0": "LGPL-3.0-only", "agpl-3.0": "AGPL-3.0-only", "isc license": "ISC"}
SPDX = re.compile(r"^[A-Za-z0-9.+-]{2,40}( (AND|OR|WITH) [A-Za-z0-9.+-]{2,40}){0,4}$")
LICENCE_FILES = ("LICENSE", "LICENCE", "COPYING", "LICENSE.md", "LICENSE.txt", "LICENCE.md", "LICENCE.txt",
                 "COPYING.md", "COPYING.txt", "LICENSE.rst")
PEP508 = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*\(?\s*([^;()]*?)\s*\)?\s*(;.*)?$")
MAIN_FILES = ("main.py", "app.py", "cli.py", "manage.py", "index.js", "main.go", "src/main.rs")


@dataclass
class RepoProfile:
    repo: str
    commit: str
    url: str
    folder: str
    verdict: str
    findings: list[str] = field(default_factory=list)
    licence: str = "unknown"
    languages: dict[str, int] = field(default_factory=dict)
    size_files: int = 0
    size_bytes: int = 0
    entry_points: list[str] = field(default_factory=list)
    dependencies: list[dict] = field(default_factory=list)
    readme_summary: str = ""
    own: bool = False
    made: float = 0.0


def _read(path: Path, limit: int = MAX_READ) -> str:
    """Up to `limit` bytes of a regular file as text; '' for links, folders or anything unreadable."""
    try:
        if path.is_symlink() or not path.is_file():
            return ""
        with open(path, "rb") as f:
            return f.read(limit).decode("utf-8", errors="replace")
    except OSError:
        return ""


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(_read(path))
    except (tomllib.TOMLDecodeError, ValueError):
        return {}


def _json(path: Path) -> dict:
    try:
        data = json.loads(_read(path) or "{}")
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _cfg(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    try:
        cp.read_string(_read(path))
    except configparser.Error:
        pass
    return cp


def _spdx(value) -> str:
    if isinstance(value, dict):
        value = value.get("text") or value.get("type") or ""
    text = str(value or "").strip()
    if not text:
        return ""
    if text.lower() in ALIASES:
        return ALIASES[text.lower()]
    return text if SPDX.fullmatch(text) else ""


def detect_licence(folder: Path) -> str:
    """SPDX id from the licence file's text first, then package metadata; 'unknown' if neither says."""
    folder = Path(folder)
    for name in LICENCE_FILES:
        text = _read(folder / name, 200_000)
        if not text:
            continue
        for spdx, phrases in LICENCE_TEXT:
            if all(re.search(p, text, re.I) for p in phrases):
                return spdx
    meta = [
        _toml(folder / "pyproject.toml").get("project", {}).get("license"),
        (_toml(folder / "pyproject.toml").get("project", {}).get("license-expression")),
        _json(folder / "package.json").get("license"),
        _toml(folder / "Cargo.toml").get("package", {}).get("license"),
        _cfg(folder / "setup.cfg").get("metadata", "license", fallback=""),
    ]
    for value in meta:
        spdx = _spdx(value)
        if spdx:
            return spdx
    return "unknown"


def _pep508(line: str, source: str) -> dict | None:
    m = PEP508.match(line)
    if not m:
        return None
    return {"name": m.group(1), "version": m.group(3).replace(" ", ""), "source": source}


def detect_dependencies(folder: Path) -> list[dict]:
    folder = Path(folder)
    out: list[dict] = []

    def add(dep):
        if dep and dep not in out and len(out) < 500:
            out.append(dep)

    py = _toml(folder / "pyproject.toml")
    for line in py.get("project", {}).get("dependencies", []) or []:
        add(_pep508(str(line), "pyproject.toml"))
    poetry = py.get("tool", {}).get("poetry", {}).get("dependencies", {}) or {}
    for name, spec in poetry.items():
        if str(name).lower() != "python":
            add({"name": str(name), "version": str(spec.get("version", "") if isinstance(spec, dict) else spec),
                 "source": "pyproject.toml"})
    for req in sorted(folder.glob("requirements*.txt")):
        for line in _read(req).splitlines():
            line = line.split(" #", 1)[0].strip()
            if not line or line.startswith(("#", "-", "http:", "https:", "git+", ".", "/")):
                continue
            add(_pep508(line, req.name))
    cfg = _cfg(folder / "setup.cfg")
    for line in cfg.get("options", "install_requires", fallback="").splitlines():
        if line.strip():
            add(_pep508(line, "setup.cfg"))
    pkg = _json(folder / "package.json")
    for key, source in (("dependencies", "package.json"), ("devDependencies", "package.json (dev)")):
        deps = pkg.get(key)
        if isinstance(deps, dict):
            for name, ver in deps.items():
                add({"name": str(name), "version": str(ver), "source": source})
    gomod = _read(folder / "go.mod")
    block = False
    for raw in gomod.splitlines():
        line = raw.split("//", 1)[0].strip()
        if line.startswith("require ("):
            block = True
            continue
        if block and line == ")":
            block = False
            continue
        if line.startswith("require "):
            line = line[len("require "):].strip()
        elif not block:
            continue
        parts = line.split()
        if len(parts) >= 2:
            add({"name": parts[0], "version": parts[1], "source": "go.mod"})
    cargo = _toml(folder / "Cargo.toml").get("dependencies", {}) or {}
    for name, spec in cargo.items():
        add({"name": str(name), "version": str(spec.get("version", "") if isinstance(spec, dict) else spec),
             "source": "Cargo.toml"})
    return out


def detect_entry_points(folder: Path, files: list[str]) -> list[str]:
    folder = Path(folder)
    out: list[str] = []

    def add(item: str):
        if item and item not in out and len(out) < 50:
            out.append(item)

    scripts = _toml(folder / "pyproject.toml").get("project", {}).get("scripts", {}) or {}
    for name, target in scripts.items():
        add(f"{name} = {target}")
    for line in _cfg(folder / "setup.cfg").get("options.entry_points", "console_scripts", fallback="").splitlines():
        if "=" in line:
            name, target = line.split("=", 1)
            add(f"{name.strip()} = {target.strip()}")
    pkg = _json(folder / "package.json")
    bins = pkg.get("bin")
    if isinstance(bins, str):
        add(f"{pkg.get('name', 'bin')} = {bins}")
    elif isinstance(bins, dict):
        for name, target in bins.items():
            add(f"{name} = {target}")
    if isinstance(pkg.get("main"), str):
        add(pkg["main"].removeprefix("./"))
    for rel in files:
        if rel.endswith("/__main__.py") or rel == "__main__.py" or rel in MAIN_FILES or \
                re.fullmatch(r"cmd/[^/]+/main\.go", rel):
            add(rel)
    return out


def _walk(folder: Path) -> tuple[list[str], int]:
    files: list[str] = []
    size = 0
    for root, dirs, names in os.walk(folder):        # never follows links
        dirs[:] = sorted(d for d in dirs if d != ".git")
        for name in sorted(names):
            path = Path(root) / name
            rel = path.relative_to(folder).as_posix()
            if rel == PROFILE_NAME or path.is_symlink():
                continue
            files.append(rel)
            try:
                size += path.stat().st_size
            except OSError:
                pass
    return files, size


def _readme(folder: Path, repo: str) -> str:
    for path in sorted(Path(folder).iterdir()):
        if path.is_file() and path.name.lower().split(".")[0] == "readme":
            text = _read(path, README_CHARS * 4)[:README_CHARS]
            return untrusted(text, f"README of {repo}")
    return ""


def make_profile(folder: Path, repo: str, commit: str, own: bool, report=None, defender=None) -> RepoProfile:
    """Profile an unpacked repo. `report` is repo_check's Report if it was already scanned; otherwise it is scanned
    now (`defender` is passed through to repo_check, for tests)."""
    from prometheus import repo_check

    folder = Path(folder)
    if report is None:
        report = repo_check.scan(folder, defender) if defender else repo_check.scan(folder)
    files, size = _walk(folder)
    langs: dict[str, int] = {}
    for rel in files:
        lang = LANGUAGES.get(Path(rel).suffix.lower())
        if lang:
            langs[lang] = langs.get(lang, 0) + 1
    return RepoProfile(
        repo=repo, commit=commit, url=f"https://github.com/{repo}", folder=str(folder), verdict=report.verdict,
        findings=[f"{what}: {where}" for _level, what, where in report.findings], licence=detect_licence(folder),
        languages=langs, size_files=len(files), size_bytes=size, entry_points=detect_entry_points(folder, files),
        dependencies=detect_dependencies(folder), readme_summary=_readme(folder, repo), own=bool(own),
        made=time.time())


def save(profile: RepoProfile) -> Path:
    """Write profile.json into the profile's folder (temporary file, then an atomic replace)."""
    target = Path(profile.folder) / PROFILE_NAME
    tmp = target.with_name(PROFILE_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(asdict(profile), f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
    return target


def load(folder: Path) -> RepoProfile:
    data = json.loads((Path(folder) / PROFILE_NAME).read_text(encoding="utf-8"))
    names = {f.name for f in fields(RepoProfile)}
    return RepoProfile(**{k: v for k, v in data.items() if k in names})
