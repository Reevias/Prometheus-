"""The owner's skills library on GitHub (M4.5): ``<owner>/prometheus-skills`` (private).

The library repository holds ``index.json`` and one folder per skill (``SKILL.md``, ``manifest.json``, ``scripts/``):

    {"skills": [{"name": "...", "description": "one line", "version": "1.0.0", "commit": "...",
                 "files": {"SKILL.md": "<sha256>", "scripts/x.py": "<sha256>", ...}}]}

* ``refresh_index()`` downloads only ``index.json`` (a few KB) from one snapshot (commit) of the default branch and keeps
  it in ``<data>/skills-library/index.json``. The planner sees names and one-line descriptions only.
* ``load(name)`` downloads that skill's files from the same snapshot, checks **every** file's sha256 against the index
  (any difference: refused, nothing cached), and caches it in ``<data>/skills-cache/<name>@<version>/``. A cached copy
  is checked again on every load. Offline, cached skills still work; others are "not downloaded yet".
  Security rests only on the sha256 values: an entry's ``commit`` is information (where it was added), so the library
  keeps working after any kind of merge (merge, squash or rebase).
* ``prune()`` removes cached skills not used for 30 days.
* ``publish(folder)`` (after the owner's yes) opens a pull request: a new branch ``skill/<name>-<version>-<time>``
  with commit 1 adding the skill's files and commit 2 adding its entry to ``index.json``. It never writes to the
  default branch. The owner merges it on GitHub.

The key is ``SKILLS_LIBRARY_TOKEN`` ("GitHub skills library" in Settings), sent only to api.github.com. Make it like this:
GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token. Pick
"Only select repositories" and choose only ``prometheus-skills``. Under Repository permissions set **Contents** and
**Pull requests** to "Read and write" (Metadata "Read-only" is added by itself). Nothing else. Set an expiry date, copy
the token and paste it into Prometheus's Settings as "GitHub skills library".
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import httpx

API = "https://api.github.com"
LIBRARY_NAME = "prometheus-skills"
TOKEN_NAME = "SKILLS_LIBRARY_TOKEN"
PUBLISH_KIND = "publish_skill"
UNUSED_DAYS = 30
REFRESH_SECONDS = 24 * 3600
MAX_INDEX = 256 * 1024
MAX_FILE = 1024 * 1024
MAX_SKILL = 5 * 1024 * 1024
MAX_FILES = 60
NAME = re.compile(r"^[a-z][a-z0-9-]{0,40}$")
VERSION = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,4}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")
PATH = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}(/[A-Za-z0-9_][A-Za-z0-9_.-]{0,80}){0,4}$")
REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/[A-Za-z0-9._-]{1,100}$")


class LibraryError(Exception):
    """A plain-words reason the library couldn't do something."""


def library_repo(settings) -> str:
    """The library sits next to the owner's Prometheus repository: <owner>/prometheus-skills."""
    owner = str(getattr(settings, "github_repo", "") or "").split("/", 1)[0]
    return f"{owner}/{LIBRARY_NAME}" if owner else ""


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_path(rel: str) -> bool:
    if not PATH.fullmatch(rel) or rel.lower() == "manifest.json.tmp":
        return False
    for part in rel.split("/"):
        stem = part.split(".")[0].upper()
        if part in (".", "..") or part.endswith((".", " ")) or stem in {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} \
                or re.fullmatch(r"(COM|LPT)[0-9]", stem):
            return False
    return True


def _valid_entry(e) -> dict | None:
    """An index entry with sane names, a version, and a sha256 for every file (SKILL.md included)."""
    if not isinstance(e, dict):
        return None
    name, desc, version = e.get("name"), e.get("description"), e.get("version")
    files = e.get("files")
    if not (isinstance(name, str) and NAME.fullmatch(name) and isinstance(desc, str) and desc.strip()
            and isinstance(version, str) and VERSION.fullmatch(version) and isinstance(files, dict)
            and 0 < len(files) <= MAX_FILES and "SKILL.md" in files):
        return None
    if not all(isinstance(p, str) and _safe_path(p) and isinstance(s, str) and SHA256.fullmatch(s)
               for p, s in files.items()):
        return None
    if len({p.lower() for p in files}) != len(files):          # Windows ignores case: no clashing names
        return None
    commit = e.get("commit")
    return {"name": name, "description": " ".join(desc.split())[:300], "version": version,
            "commit": commit if isinstance(commit, str) and len(commit) <= 64 else "", "files": dict(files)}


def _atomic_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex[:8]}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


class Library:
    def __init__(self, data: Path, repo: str = "", http: httpx.Client | None = None, token=None):
        self.data = Path(data)
        self.repo = repo
        self.home = self.data / "skills-library"
        self.cache = self.data / "skills-cache"
        self._http = http
        if token is None:
            from prometheus.config import get_secret

            token = lambda: get_secret(TOKEN_NAME)       # noqa: E731
        self._token = token
        self._lock = threading.RLock()

    # -- talking to GitHub ------------------------------------------------------------------------------------------
    def _client(self) -> httpx.Client:
        if self._http is None:
            self._http = httpx.Client(timeout=20.0, follow_redirects=False)
        return self._http

    def _headers(self, raw: bool = False) -> dict:
        if not REPO.fullmatch(self.repo or ""):
            raise LibraryError("The skills library's name isn't set (it should be <you>/prometheus-skills).")
        token = self._token() if self._token else None
        if not token:
            raise LibraryError("Add the 'GitHub skills library' key in Settings first (a fine-grained token for "
                               "prometheus-skills only).")
        accept = "application/vnd.github.raw+json" if raw else "application/vnd.github+json"
        return {"Authorization": f"Bearer {token}", "Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}

    def _call(self, method: str, path: str, body: dict | None = None, raw: bool = False, accept: str = "",
              params: dict | None = None, limit: int = MAX_INDEX) -> httpx.Response:
        headers = self._headers(raw)
        if accept:
            headers["Accept"] = accept
        url = f"{API}/repos/{self.repo}{path}"
        try:
            with self._client().stream(method, url, headers=headers, json=body, params=params,
                                       follow_redirects=False) as resp:
                if int(resp.headers.get("Content-Length") or 0) > limit:
                    raise LibraryError("GitHub sent a file that is too big for the skills library.")
                data = b""
                for chunk in resp.iter_bytes():
                    data += chunk
                    if len(data) > limit:
                        raise LibraryError("GitHub sent a file that is too big for the skills library.")
                resp = httpx.Response(resp.status_code, headers=resp.headers, content=data, request=resp.request)
        except httpx.HTTPError as e:
            raise ConnectionError(f"GitHub can't be reached ({type(e).__name__}).") from e
        if resp.status_code == 404:
            raise LibraryError(f"GitHub can't find that in {self.repo} (or the key has no access to it).")
        if resp.status_code in (401, 403):
            raise LibraryError("GitHub refused the skills library key (check it in Settings).")
        if resp.status_code >= 300:
            raise LibraryError(f"GitHub said no (error {resp.status_code}).")
        return resp

    def _json(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            got = self._call(method, path, body).json()
        except ValueError as e:
            raise LibraryError("GitHub sent an unreadable reply.") from e
        if not isinstance(got, dict):
            raise LibraryError("GitHub sent an unexpected reply.")
        return got

    def _snapshot(self) -> tuple[str, str]:
        """(default branch, the commit it points at now)."""
        branch = str(self._json("GET", "").get("default_branch") or "main")
        sha = self._call("GET", f"/commits/{quote(branch, safe='')}", accept="application/vnd.github.sha"
                         ).text.strip().lower()
        if not SHA1.fullmatch(sha):
            raise LibraryError("GitHub didn't give a full commit for the library.")
        return branch, sha

    def _file(self, path: str, ref: str, limit: int = MAX_FILE) -> bytes:
        return self._call("GET", f"/contents/{quote(path, safe='/')}", raw=True, params={"ref": ref},
                          limit=limit).content

    # -- the index -----------------------------------------------------------------------------------------------
    def refresh_index(self) -> dict:
        """Download index.json only, keep it in <data>/skills-library/index.json. Also drops old cached skills."""
        try:
            _branch, snap = self._snapshot()
            raw = self._file("index.json", snap, MAX_INDEX)
        except ConnectionError as e:
            raise LibraryError(f"The skills library can't be reached right now: {e}") from e
        try:
            got = json.loads(raw.decode("utf-8-sig"))
        except (ValueError, UnicodeDecodeError) as e:
            raise LibraryError("The library's index.json isn't readable.") from e
        entries, seen = [], set()
        for e in got.get("skills", []) if isinstance(got, dict) else []:
            ok = _valid_entry(e)
            if ok and ok["name"] not in seen:
                seen.add(ok["name"])
                entries.append(ok)
        index = {"repo": self.repo, "snapshot": snap, "fetched": time.time(), "skills": entries}
        with self._lock:
            _atomic_json(self.home / "index.json", index)
        self.prune()
        return index

    def index(self) -> dict:
        """The saved index (no network); empty when it was never downloaded."""
        try:
            got = json.loads((self.home / "index.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"skills": []}
        if not isinstance(got, dict) or got.get("repo") != self.repo:
            return {"skills": []}
        return got

    def entries(self) -> list[dict]:
        return [e for e in (_valid_entry(x) for x in self.index().get("skills", [])) if e]

    def entry(self, name: str) -> dict | None:
        return next((e for e in self.entries() if e["name"] == name), None)

    # -- the cache -----------------------------------------------------------------------------------------------
    def _used(self) -> dict:
        try:
            got = json.loads((self.home / "used.json").read_text(encoding="utf-8"))
            return got if isinstance(got, dict) else {}
        except (OSError, ValueError):
            return {}

    def _mark_used(self, key: str, when: float | None = None) -> None:
        with self._lock:
            used = self._used()
            used[key] = float(when if when is not None else time.time())
            _atomic_json(self.home / "used.json", used)

    def _cached_ok(self, folder: Path, entry: dict) -> bool:
        """Every file there, no extra ones, no links, every sha256 matching the index."""
        if folder.is_symlink() or not folder.is_dir():
            return False
        found = {}
        for path in folder.rglob("*"):
            if path.is_symlink():
                return False
            if path.is_file():
                found[path.relative_to(folder).as_posix()] = path
        if set(found) != set(entry["files"]):
            return False
        try:
            return all(_file_sha(found[rel]) == sha for rel, sha in entry["files"].items())
        except OSError:
            return False

    def load(self, name: str) -> Path:
        """The skill's folder in the cache, downloading and checking it the first time."""
        entry = self.entry(name)
        if entry is None:
            raise LibraryError(f"/{name} isn't in your skills library.")
        key = f"{name}@{entry['version']}"
        folder = self.cache / key
        with self._lock:
            if folder.exists() or folder.is_symlink():
                if self._cached_ok(folder, entry):
                    self._mark_used(key)
                    return folder
                if folder.is_symlink():
                    folder.unlink()
                else:
                    shutil.rmtree(folder, ignore_errors=True)        # changed on disk: never trusted
            snap = self.index().get("snapshot", "")
            if not SHA1.fullmatch(str(snap)):
                raise LibraryError(f"/{name} is not downloaded yet: refresh the skills library first.")
            self.cache.mkdir(parents=True, exist_ok=True)
            work = self.cache / f".tmp-{key}-{uuid.uuid4().hex[:8]}"
            work.mkdir()
            try:
                total = 0
                for rel, want in entry["files"].items():
                    try:
                        data = self._file(f"{name}/{rel}", snap)
                    except ConnectionError as e:
                        raise LibraryError(f"/{name} is not downloaded yet, and GitHub can't be reached right now. "
                                           "It will download next time you're online.") from e
                    total += len(data)
                    if total > MAX_SKILL:
                        raise LibraryError(f"/{name} is too big for the skills library, so it was refused.")
                    if _sha(data) != want:
                        raise LibraryError(f"/{name} was refused: its file {rel} doesn't match the library's index "
                                           "(it may have been changed). Nothing was kept.")
                    target = work.joinpath(*rel.split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, "xb") as f:
                        f.write(data)
                os.rename(work, folder)
            except BaseException:
                shutil.rmtree(work, ignore_errors=True)
                raise
            self._mark_used(key)
            return folder

    def prune(self, now: float | None = None, days: int = UNUSED_DAYS) -> list[str]:
        """Remove cached skills not used for `days` days. Returns what was removed."""
        now = time.time() if now is None else now
        removed = []
        with self._lock:
            if not self.cache.is_dir():
                return removed
            used = self._used()
            for folder in sorted(self.cache.iterdir()):
                key = folder.name
                if key.startswith(".tmp-"):
                    if now - folder.stat().st_mtime > 3600:
                        shutil.rmtree(folder, ignore_errors=True)
                    continue
                last = used.get(key)
                if last is None:
                    try:
                        last = folder.stat().st_mtime
                    except OSError:
                        continue
                if now - float(last) > days * 86400:
                    if folder.is_symlink():
                        folder.unlink()
                    else:
                        shutil.rmtree(folder, ignore_errors=True)
                    used.pop(key, None)
                    removed.append(key)
            if removed:
                _atomic_json(self.home / "used.json", used)
        return removed

    # -- publishing (after the owner's yes) -----------------------------------------------------------------------
    def _skill_files(self, folder: Path) -> tuple[dict, dict[str, bytes]]:
        folder = Path(folder)
        if folder.is_symlink() or not folder.is_dir():
            raise LibraryError("That skill folder doesn't exist.")
        try:
            manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise LibraryError("Only skills with a manifest.json can be published.") from e
        if not isinstance(manifest, dict) or manifest.get("name") != folder.name or not NAME.fullmatch(folder.name) \
                or not VERSION.fullmatch(str(manifest.get("version", ""))) or not isinstance(manifest.get("files"), dict):
            raise LibraryError("The skill's manifest.json isn't valid, so it wasn't published.")
        files: dict[str, bytes] = {}
        for path in sorted(folder.rglob("*")):
            rel = path.relative_to(folder).as_posix()
            if path.is_symlink():
                raise LibraryError(f"The skill has a link ({rel}); links are never published.")
            if path.is_file():
                if not _safe_path(rel):
                    raise LibraryError(f"The skill has a file name that isn't allowed ({rel}).")
                files[rel] = path.read_bytes()
        if len(files) > MAX_FILES or sum(map(len, files.values())) > MAX_SKILL:
            raise LibraryError("The skill is too big to publish.")
        listed = manifest["files"]
        others = {rel: _sha(data) for rel, data in files.items() if rel != "manifest.json"}
        if others != listed:
            raise LibraryError("The skill's files don't match its manifest.json, so it wasn't published.")
        return manifest, files

    def publish(self, folder: Path) -> str:
        """Open a pull request adding the skill (and its index entry) on a new branch. Returns the pull request's
        address. Never writes to the default branch."""
        manifest, files = self._skill_files(Path(folder))
        name, version = manifest["name"], manifest["version"]
        try:
            branch_main = str(self._json("GET", "").get("default_branch") or "main")
            base = str(self._json("GET", f"/git/ref/heads/{quote(branch_main, safe='')}").get("object", {}).get("sha", ""))
            if not SHA1.fullmatch(base):
                raise LibraryError("GitHub didn't give the library's latest commit.")
            base_tree = str(self._json("GET", f"/git/commits/{base}").get("tree", {}).get("sha", ""))

            def blob(data: bytes) -> str:
                return str(self._json("POST", "/git/blobs", {"content": base64.b64encode(data).decode("ascii"),
                                                             "encoding": "base64"})["sha"])

            def commit(tree_items: list[dict], parent_tree: str, parent: str, message: str) -> tuple[str, str]:
                tree = str(self._json("POST", "/git/trees", {"base_tree": parent_tree, "tree": tree_items})["sha"])
                sha = str(self._json("POST", "/git/commits", {"message": message, "tree": tree,
                                                              "parents": [parent]})["sha"])
                return sha, tree

            items = [{"path": f"{name}/{rel}", "mode": "100644", "type": "blob", "sha": blob(data)}
                     for rel, data in files.items()]
            first, tree1 = commit(items, base_tree, base, f"Add skill /{name} {version}")
            branch = f"skill/{name}-{version}-{int(time.time())}"
            self._json("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": first})
            try:
                old = json.loads(self._file("index.json", base, MAX_INDEX).decode("utf-8-sig"))
            except LibraryError:
                old = {"skills": []}
            except ValueError as e:
                raise LibraryError("The library's index.json isn't readable, so nothing was published.") from e
            skills = [e for e in (old.get("skills", []) if isinstance(old, dict) else [])
                      if isinstance(e, dict) and e.get("name") != name]
            skills.append({"name": name, "description": " ".join(str(manifest.get("description", "")).split())[:300]
                           or f"Skill {name}", "version": version, "commit": first,
                           "files": {rel: _sha(data) for rel, data in files.items()}})
            index_bytes = (json.dumps({"skills": skills}, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
            second, _ = commit([{"path": "index.json", "mode": "100644", "type": "blob", "sha": blob(index_bytes)}],
                               tree1, first, f"List /{name} {version} in index.json")
            self._json("PATCH", f"/git/refs/heads/{quote(branch, safe='/')}", {"sha": second, "force": False})
            pr = self._json("POST", "/pulls", {
                "title": f"Add skill /{name} {version}", "head": branch, "base": branch_main,
                "body": (f"Prometheus published the skill /{name} {version} after the owner said yes.\n\n"
                         "Merge it to add it to the library; every file is checked by its sha256 in index.json.")})
        except ConnectionError as e:
            raise LibraryError(f"GitHub can't be reached, so /{name} wasn't published: {e}") from e
        url = str(pr.get("html_url", ""))
        if not url.startswith(f"https://github.com/{self.repo}/pull/"):
            raise LibraryError("GitHub didn't confirm the pull request.")
        return url


def publish_handler(library: Library, skills_dir: Path, payload: dict, store) -> str:
    """The approval: the owner said yes to publishing one of their skills. Only a skill whose sandbox trial (M4.6)
    passed on exactly these files is published: a library skill is loaded on every device without a new trial."""
    from prometheus.repos.trial import load_result

    name = str(payload.get("name", ""))
    if not NAME.fullmatch(name):
        raise LibraryError("That isn't a skill name.")
    tested = load_result(store, name)
    if tested is None or not tested.still_valid(Path(skills_dir) / name):
        raise LibraryError(f"/{name} wasn't published: it has to pass its trial run in the sandbox first (switch it on "
                           "in Skills to test it), and nothing may change after that.")
    url = library.publish(Path(skills_dir) / name)
    return f"Opened a pull request to your skills library with /{name}: {url} (merge it on GitHub to add it)."


def start_daily_refresh(library: Library, stop: threading.Event | None = None) -> threading.Thread:
    """Refresh the index when Prometheus starts and once a day; quietly does nothing without a key or offline."""
    stop = stop or threading.Event()

    def loop():
        while not stop.is_set():
            try:
                if library._token and library._token():
                    library.refresh_index()
            except Exception:       # offline or no library yet: the saved index and cache keep working
                pass
            stop.wait(REFRESH_SECONDS)

    from prometheus.jobs.runner import get_default_runner, start_job

    job = start_job("library_refresh", "Daily skills library refresh", loop, name="skills-library")
    return get_default_runner().get_thread(job.id)
