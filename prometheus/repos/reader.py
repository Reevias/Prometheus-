"""Read a GitHub repository safely (M4.1): download one pinned commit as a zip, unpack it into quarantine, check it.

1. ``ref`` (branch, tag or commit; empty = the default branch) is resolved to a 40-character commit with GitHub's
   REST API. Only that commit's zip is downloaded: no ``git clone``, so no git hooks, no submodules, no LFS.
2. The zip is refused when it is over ``MAX_BYTES`` (200 MB, downloaded or unpacked) or ``MAX_FILES`` (20 000 files).
3. It is unpacked into ``<dest>/<owner>__<name>__<sha7>/`` (``dest`` is ``<data>/quarantine``). Member names are not
   trusted: ``..``, absolute paths, drive letters, ``:`` (NTFS streams), links, Windows device names (CON, NUL,
   COM1…), names ending in a dot or space, ``.git`` and names that clash when case is ignored are refused and
   listed in ``refused``. Files are created new (never overwritten) and every one is checked to be inside the folder.
4. prometheus/repo_check.py checks the folder; ``danger`` deletes it and raises :class:`RepoDanger` with the reasons.
5. A :class:`prometheus.repos.profile.RepoProfile` is made and saved as ``profile.json`` (see that module).

Nothing from the repository is ever imported, executed or installed. The GitHub token (``get_secret("GITHUB_TOKEN")``,
limited to Prometheus's own repository) is sent only to api.github.com and only for that repository, as in
prometheus/agents/github.py; the download itself (codeload.github.com) never gets it.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote

import httpx

from prometheus.agents.github import API, repo_from
from prometheus.config import get_secret, load_settings
from prometheus.repos.profile import RepoProfile, make_profile, save
from prometheus.safety.paths import WINDOWS_RESERVED_NAMES, _inside

MAX_BYTES = 200 * 1024 * 1024
MAX_FILES = 20_000
CHUNK = 1024 * 1024
SHA = re.compile(r"^[0-9a-f]{40}$")
REF = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
DOWNLOAD_HOST = "codeload.github.com"
# Prometheus's own files in the top folder (profile.json, M4.2's codemap.json, the owner's repo notes NOTES.md with
# its lock, temporary and "broken" copies): a repo's copy would be trusted later
OWN_NAMES = {"profile.json", "profile.json.tmp", "codemap.json", "notes.md"}
OWN_PREFIXES = (".notes.md.", "notes.broken")


class RepoReadError(Exception):
    """The repository could not be read safely; the message is in plain words."""


class RepoDanger(RepoReadError):
    """repo_check said the repository is dangerous; it has been deleted."""

    def __init__(self, repo: str, reasons: list[str]):
        self.reasons = reasons
        super().__init__(f"{repo} looks dangerous, so it was deleted (nothing was run): " + "; ".join(reasons[:12]))


def _mb(n: int) -> str:
    return f"{n / (1024 * 1024):.0f} MB"


def _check_repo(repo: str) -> tuple[str, str]:
    text = str(repo or "").strip()
    norm = repo_from(text)
    if not norm or norm != text:
        raise RepoReadError(f"Not a GitHub repository name (use owner/name): {text[:80]}")
    owner, name = norm.split("/", 1)
    if name.strip(".") == "" or name.endswith((".", " ")):
        raise RepoReadError(f"Not a GitHub repository name (use owner/name): {text[:80]}")
    return owner, name


def _headers(repo: str) -> dict:
    headers = {"X-GitHub-Api-Version": "2022-11-28"}
    try:
        own = repo.lower() == (load_settings().github_repo or "").lower()
    except Exception:     # settings unreadable: send no token
        own = False
    token = get_secret("GITHUB_TOKEN") if own else ""
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _api_error(repo: str, resp: httpx.Response) -> RepoReadError:
    if resp.status_code == 404:
        return RepoReadError(f"GitHub has no public repository or commit like that in {repo} (check the name).")
    if resp.status_code in (403, 429):
        return RepoReadError("GitHub's limit for reading was reached. Try again in a while.")
    return RepoReadError(f"GitHub said no (error {resp.status_code}).")


def resolve_commit(http: httpx.Client, repo: str, ref: str = "") -> str:
    ref = str(ref or "").strip()
    if ref and (not REF.fullmatch(ref) or ".." in ref.split("/") or ref.startswith("/")):
        raise RepoReadError("That branch, tag or commit name is not allowed.")
    headers = {**_headers(repo), "Accept": "application/vnd.github.sha"}
    try:
        resp = http.get(f"{API}/repos/{repo}/commits/{quote(ref or 'HEAD', safe='/')}", headers=headers,
                        follow_redirects=False)
    except httpx.HTTPError as e:
        raise RepoReadError(f"GitHub can't be reached ({type(e).__name__}).") from e
    if resp.status_code >= 300:
        raise _api_error(repo, resp)
    sha = resp.text.strip().lower()
    if not SHA.fullmatch(sha):
        raise RepoReadError("GitHub didn't give a full commit for that name, so nothing was downloaded.")
    return sha


def _download(http: httpx.Client, repo: str, sha: str, target: Path) -> None:
    """Stream the commit's zip into `target`, stopping at MAX_BYTES."""
    try:
        resp = http.get(f"{API}/repos/{repo}/zipball/{sha}", headers={**_headers(repo),
                        "Accept": "application/vnd.github+json"}, follow_redirects=False)
        if resp.status_code in (301, 302, 303, 307, 308):
            url = httpx.URL(resp.headers.get("Location", ""))
            if url.scheme != "https" or url.host != DOWNLOAD_HOST:
                raise RepoReadError("GitHub sent the download to an unexpected place, so it was stopped.")
            req = http.build_request("GET", url)            # no token for the download host
        elif resp.status_code == 200:
            req = None
        else:
            raise _api_error(repo, resp)
        stream = http.send(req, stream=True, follow_redirects=False) if req is not None else resp
        try:
            if stream.status_code != 200:
                raise _api_error(repo, stream)
            declared = int(stream.headers.get("Content-Length") or 0)
            if declared > MAX_BYTES:
                raise RepoReadError(f"The repository is too big ({_mb(declared)}; the limit is {_mb(MAX_BYTES)}).")
            got = 0
            with open(target, "xb") as f:
                for chunk in stream.iter_bytes(CHUNK):
                    got += len(chunk)
                    if got > MAX_BYTES:
                        raise RepoReadError(f"The repository is too big (over {_mb(MAX_BYTES)}).")
                    f.write(chunk)
        finally:
            stream.close()
    except httpx.HTTPError as e:
        raise RepoReadError(f"The download failed ({type(e).__name__}).") from e


def _bad_part(part: str) -> str:
    if part in (".", ".."):
        return "goes outside the folder"
    if ":" in part:
        return "has a ':' (drive or hidden stream)"
    if part.endswith((".", " ")):
        return "ends in a dot or space"
    if part.split(".")[0].rstrip(" .").strip().upper() in WINDOWS_RESERVED_NAMES:
        return "is a Windows device name"
    if part.lower() == ".git":
        return "is a .git folder"
    if any(ord(c) < 32 or c in '<>"|?*' for c in part):
        return "has characters Windows doesn't allow"
    return ""


def _unpack(zip_path: Path, folder: Path, refused: list[str]) -> None:
    try:
        z = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e:
        raise RepoReadError("The download is not a readable zip.") from e
    with z:
        infos = z.infolist()
        files = [i for i in infos if not i.is_dir()]
        if len(files) > MAX_FILES or len(infos) > 2 * MAX_FILES:      # folders count too (each one is made on disk)
            raise RepoReadError(f"The repository has too many files or folders ({len(files)} files, "
                                f"{len(infos) - len(files)} folders; the limit is {MAX_FILES} files).")
        declared = sum(i.file_size for i in files)
        if declared > MAX_BYTES:
            raise RepoReadError(f"The repository is too big unpacked ({_mb(declared)}; the limit is "
                                f"{_mb(MAX_BYTES)}).")
        # GitHub's zip starts with one top folder entry (owner-name-sha7/); it is dropped from every path
        top = infos[0].filename.rstrip("/") if infos and infos[0].is_dir() and infos[0].filename.count("/") == 1 \
            else None
        root = folder.resolve()
        seen: set[str] = set()
        written = 0
        for info in infos:
            name = info.filename
            if name.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", name):
                refused.append(f"{name} (absolute path)")
                continue
            parts = re.split(r"[\\/]", name)
            if top is not None and parts[0] == top:
                parts = parts[1:]
            parts = [p for p in parts if p != ""]
            if not parts:
                continue
            why = next((w for w in map(_bad_part, parts) if w), "")
            if not why and (parts[0].lower() in OWN_NAMES or parts[0].lower().startswith(OWN_PREFIXES)):
                why = "has the name of a file Prometheus keeps there"
            if why:
                refused.append(f"{name} ({why})")
                continue
            kind = stat.S_IFMT(info.external_attr >> 16)
            if kind and kind not in (stat.S_IFREG, stat.S_IFDIR):
                refused.append(f"{name} (a link or special file)")
                continue
            rel = "/".join(parts)
            target = folder.joinpath(*parts)
            if info.is_dir():
                try:
                    target.mkdir(parents=True, exist_ok=True)
                except OSError as e:
                    refused.append(f"{name} (can't be saved on this computer: {e.strerror or type(e).__name__})")
                continue
            key = rel.lower()
            if key in seen:
                refused.append(f"{name} (same name as another file when case is ignored)")
                continue
            seen.add(key)
            # a file and a folder with the same name, or (on Windows) a path that is too long: refuse that one file
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                if not _inside(target.parent.resolve(), root) or target.parent.is_symlink():
                    refused.append(f"{name} (goes outside the folder)")
                    continue
                out = open(target, "xb")
            except OSError as e:
                refused.append(f"{name} (can't be saved on this computer: {e.strerror or type(e).__name__})")
                continue
            with z.open(info) as src, out as dst:
                while chunk := src.read(CHUNK):
                    written += len(chunk)
                    if written > MAX_BYTES:
                        raise RepoReadError(f"The repository is too big unpacked (over {_mb(MAX_BYTES)}).")
                    dst.write(chunk)


def read_repo(repo: str, ref: str = "", dest: Path | None = None, own: bool = False, http: httpx.Client | None = None,
              defender=None, refused: list[str] | None = None) -> RepoProfile:
    """Download, unpack, check and profile `repo` at `ref`. Returns the saved profile. `own`: the owner's own repo
    (see prometheus/repos/own.py ``is_own``); it makes repo_check's pattern matches "check this first".
    Raises RepoReadError (plain message) or RepoDanger (folder already deleted, `.reasons`)."""
    from prometheus.repo_check import check_and_keep

    if dest is None:
        raise RepoReadError("No quarantine folder was given.")
    owner, name = _check_repo(repo)
    repo = f"{owner}/{name}"
    refused = refused if refused is not None else []
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    client = http or httpx.Client(timeout=60.0, follow_redirects=False)
    try:
        sha = resolve_commit(client, repo, ref)
        final = dest / f"{owner}__{name}__{sha[:7]}"
        if final.exists() or final.is_symlink():
            raise RepoReadError(f"{repo} at {sha[:7]} is already downloaded ({final}).")
        tag = uuid.uuid4().hex[:8]
        zip_path = dest / f".download-{sha[:7]}-{tag}.zip"
        work = dest / f".unpack-{sha[:7]}-{tag}"
        try:
            _download(client, repo, sha, zip_path)
            work.mkdir()
            _unpack(zip_path, work, refused)
            os.rename(work, final)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise
        finally:
            zip_path.unlink(missing_ok=True)
    finally:
        if http is None:
            client.close()
    # own (the caller decides it from the repo's GitHub owner, never from the files): pattern matches are "caution"
    report = check_and_keep(final, defender, own=own) if defender else check_and_keep(final, own=own)
    if report.verdict == "danger":
        shutil.rmtree(final, ignore_errors=True)
        raise RepoDanger(repo, [f"{what}: {where}" for level, what, where in report.findings if level == "danger"])
    profile = make_profile(final, repo, sha, own, report=report)
    save(profile)
    return profile


def fetch(repo: str, ref: str = "", dest: Path | None = None, **kw) -> Path:
    """Same as read_repo, returning the quarantine folder (its profile.json is already saved)."""
    return Path(read_repo(repo, ref, dest, **kw).folder)
