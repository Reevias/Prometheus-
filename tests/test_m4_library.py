"""M4.5 skills library on GitHub: only index.json is kept; a skill is downloaded on first use, every file's sha256 is
checked against the index, it is cached, and dropped after 30 unused days. Publishing is a pull request on a new
branch, never a push to main. GitHub is faked in memory; real GitHub is never touched."""
from __future__ import annotations

import base64
import hashlib
import json
import time
from pathlib import Path

import httpx
import pytest

from prometheus.repos import library as lib
from prometheus.skillbook import SkillBook

REPO = "owner/prometheus-skills"
SNAP = "c" * 40
SKILL_MD = "---\nname: word-tools\ndescription: Count the most common words in a text\n---\n\nUse scripts/count.py.\n"
SCRIPT = "print('hi')\n"


def h(text: str | bytes) -> str:
    return hashlib.sha256(text.encode("utf-8") if isinstance(text, str) else text).hexdigest()


def entry(name="word-tools", files=None, commit="0" * 40, version="1.0.0", description="Count words in a text"):
    files = files if files is not None else {"SKILL.md": h(SKILL_MD), "scripts/count.py": h(SCRIPT)}
    return {"name": name, "description": description, "version": version, "commit": commit, "files": files}


class FakeGitHub:
    """Just enough of GitHub's REST API: one snapshot of the default branch, plus the git data API for publishing."""

    def __init__(self, files: dict[str, str | bytes], online=True):
        self.files = dict(files)
        self.online = online
        self.calls: list[httpx.Request] = []
        self.n = 0
        self.blobs: dict[str, bytes] = {}
        self.trees: list[dict] = []

    def _id(self, kind):
        self.n += 1
        return f"{kind}{self.n:03d}"

    def handler(self, req: httpx.Request) -> httpx.Response:
        if not self.online:
            raise httpx.ConnectError("offline", request=req)
        self.calls.append(req)
        p, m = req.url.path, req.method
        base = f"/repos/{REPO}"
        if req.url.host != "api.github.com" or not p.startswith(base):
            return httpx.Response(404)
        p = p[len(base):]
        if m == "GET" and p == "":
            return httpx.Response(200, json={"default_branch": "main", "private": True})
        if m == "GET" and p == "/commits/main":
            return httpx.Response(200, text=SNAP)
        if m == "GET" and p.startswith("/contents/"):
            path = p[len("/contents/"):]
            if req.url.params.get("ref") != SNAP or path not in self.files:
                return httpx.Response(404)
            data = self.files[path]
            return httpx.Response(200, content=data.encode("utf-8") if isinstance(data, str) else data)
        if m == "GET" and p == "/git/ref/heads/main":
            return httpx.Response(200, json={"object": {"sha": SNAP}})
        if m == "GET" and p.startswith("/git/commits/"):
            return httpx.Response(200, json={"sha": p.rsplit("/", 1)[1], "tree": {"sha": "tree000"}})
        body = json.loads(req.content or b"{}")
        if m == "POST" and p == "/git/blobs":
            sha = self._id("blob")
            self.blobs[sha] = base64.b64decode(body["content"])
            return httpx.Response(201, json={"sha": sha})
        if m == "POST" and p == "/git/trees":
            self.trees.append(body)
            return httpx.Response(201, json={"sha": self._id("tree")})
        if m == "POST" and p == "/git/commits":
            return httpx.Response(201, json={"sha": self._id("commit")})
        if m == "POST" and p == "/git/refs":
            return httpx.Response(201, json={"ref": body["ref"]})
        if m == "PATCH" and p.startswith("/git/refs/heads/"):
            return httpx.Response(200, json={})
        if m == "POST" and p == "/pulls":
            return httpx.Response(201, json={"number": 7, "html_url": f"https://github.com/{REPO}/pull/7"})
        return httpx.Response(404)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def library_files(*entries, extra=None):
    files = {"index.json": json.dumps({"skills": list(entries)}), "word-tools/SKILL.md": SKILL_MD,
             "word-tools/scripts/count.py": SCRIPT}
    files.update(extra or {})
    return files


def make(tmp_path, gh: FakeGitHub, token="TOKEN") -> lib.Library:
    return lib.Library(tmp_path, repo=REPO, http=gh.client(), token=lambda: token)


# -- the index ------------------------------------------------------------------------------------------------

def test_refresh_stores_only_the_index_and_the_skill_list_shows_it(tmp_path):
    gh = FakeGitHub(library_files(entry()))
    library = make(tmp_path, gh)
    got = library.refresh_index()
    assert [e["name"] for e in got["skills"]] == ["word-tools"]
    saved = json.loads((tmp_path / "skills-library" / "index.json").read_text(encoding="utf-8"))
    assert saved["skills"] == [entry()] and saved["snapshot"] == SNAP and saved["repo"] == REPO
    paths = [r.url.path for r in gh.calls]
    assert not any("word-tools" in p for p in paths)                   # no skill was downloaded
    assert not (tmp_path / "skills-cache").exists()
    book = SkillBook(tmp_path / "builtin", tmp_path / "skills")
    book.library = library
    assert "- word-tools: Count words in a text" in book.catalog_text()
    stub = book.get_any("word-tools")
    assert stub.source == "library" and stub.body == ""
    assert not (tmp_path / "skills-cache").exists()                    # listing never downloads


def test_bad_index_entries_are_dropped(tmp_path):
    bad = [entry(name="Bad Name"), entry(name="up", files={"../evil.py": h("x"), "SKILL.md": h("y")}),
           entry(name="nohash", files={"SKILL.md": "abc"}), entry(name="noguide", files={"a.py": h("a")}),
           entry(name="abs", files={"/etc/x": h("x"), "SKILL.md": h("y")}), entry(name="ver", version="one")]
    gh = FakeGitHub(library_files(entry(), *bad))
    got = make(tmp_path, gh).refresh_index()
    assert [e["name"] for e in got["skills"]] == ["word-tools"]


def test_no_key_says_so_plainly(tmp_path):
    gh = FakeGitHub(library_files(entry()))
    with pytest.raises(lib.LibraryError, match="key"):
        make(tmp_path, gh, token=None).refresh_index()
    assert gh.calls == []


def test_the_token_goes_only_to_the_github_api(tmp_path):
    gh = FakeGitHub(library_files(entry()))
    make(tmp_path, gh).refresh_index()
    assert gh.calls and all(r.url.host == "api.github.com" and r.headers["authorization"] == "Bearer TOKEN"
                            for r in gh.calls)


# -- loading a skill --------------------------------------------------------------------------------------------

def test_load_downloads_checks_and_caches(tmp_path):
    gh = FakeGitHub(library_files(entry()))
    library = make(tmp_path, gh)
    library.refresh_index()
    folder = library.load("word-tools")
    assert folder == tmp_path / "skills-cache" / "word-tools@1.0.0"
    assert (folder / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert (folder / "scripts" / "count.py").read_text(encoding="utf-8") == SCRIPT
    refs = {r.url.params.get("ref") for r in gh.calls if "/contents/" in r.url.path}
    assert refs == {SNAP}                                              # the same snapshot as the index
    book = SkillBook(tmp_path / "builtin", tmp_path / "skills")
    book.library = library
    skill = book.get("word-tools")
    assert skill.source == "library" and "Use scripts/count.py." in skill.body


def test_a_changed_file_is_refused_and_nothing_is_cached(tmp_path):
    gh = FakeGitHub(library_files(entry(), extra={"word-tools/scripts/count.py": "import os; os.system('x')\n"}))
    library = make(tmp_path, gh)
    library.refresh_index()
    with pytest.raises(lib.LibraryError) as e:
        library.load("word-tools")
    assert "scripts/count.py" in str(e.value) and "refused" in str(e.value)
    cache = tmp_path / "skills-cache"
    assert not cache.exists() or list(cache.iterdir()) == []


def test_only_the_sha256_values_count_not_the_commit(tmp_path):
    """The owner's rule: the index must survive a squash merge, so the commit is only information."""
    gh = FakeGitHub(library_files(entry(commit="f" * 40)))             # a commit that doesn't exist any more
    library = make(tmp_path, gh)
    library.refresh_index()
    assert library.load("word-tools").is_dir()                         # every sha256 matches: accepted
    gh2 = FakeGitHub(library_files(entry(name="word-tools", version="1.0.1", commit="f" * 40),
                                   extra={"word-tools/SKILL.md": SKILL_MD + "one more line\n"}))
    lib2 = make(tmp_path / "two", gh2)
    lib2.refresh_index()
    with pytest.raises(lib.LibraryError, match="SKILL.md"):
        lib2.load("word-tools")                                        # one file differs: refused


def test_cached_skill_works_offline_and_uncached_says_not_downloaded_yet(tmp_path):
    gh = FakeGitHub(library_files(entry(), entry(name="other-skill", files={"SKILL.md": h("z")})))
    library = make(tmp_path, gh)
    library.refresh_index()
    library.load("word-tools")
    gh.online = False
    assert library.load("word-tools") == tmp_path / "skills-cache" / "word-tools@1.0.0"
    with pytest.raises(lib.LibraryError, match="not downloaded yet"):
        library.load("other-skill")
    with pytest.raises(lib.LibraryError, match="isn't in your skills library"):
        library.load("no-such-skill")


def test_a_cache_changed_on_disk_is_not_trusted(tmp_path):
    gh = FakeGitHub(library_files(entry()))
    library = make(tmp_path, gh)
    library.refresh_index()
    folder = library.load("word-tools")
    (folder / "scripts" / "count.py").write_text("print('changed')\n", encoding="utf-8")
    gh.online = False
    with pytest.raises(lib.LibraryError, match="not downloaded yet"):
        library.load("word-tools")
    assert not folder.exists()
    gh.online = True
    assert (library.load("word-tools") / "scripts" / "count.py").read_text(encoding="utf-8") == SCRIPT


def test_unused_for_31_days_is_removed(tmp_path):
    gh = FakeGitHub(library_files(entry(), entry(name="fresh-skill", files={"SKILL.md": h(SKILL_MD.replace(
        "word-tools", "fresh-skill"))}), extra={"fresh-skill/SKILL.md": SKILL_MD.replace("word-tools", "fresh-skill")}))
    library = make(tmp_path, gh)
    library.refresh_index()
    old = library.load("word-tools")
    fresh = library.load("fresh-skill")
    now = time.time()
    library._mark_used("word-tools@1.0.0", now - 31 * 86400)
    library._mark_used("fresh-skill@1.0.0", now - 29 * 86400)
    removed = library.prune(now=now)
    assert removed == ["word-tools@1.0.0"]
    assert not old.exists() and fresh.exists()


# -- publishing ------------------------------------------------------------------------------------------------

def _skill_folder(tmp_path) -> Path:
    folder = tmp_path / "skills" / "repo-owner-tool"
    (folder / "scripts").mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL_MD.replace("word-tools", "repo-owner-tool"), encoding="utf-8")
    (folder / "scripts" / "count.py").write_text(SCRIPT, encoding="utf-8")
    files = {"SKILL.md": h((folder / "SKILL.md").read_bytes()), "scripts/count.py": h(SCRIPT)}
    manifest = {"name": "repo-owner-tool", "version": "1.0.0", "description": "Count words",
                "source": {"repo": "owner/tool", "commit": "b" * 40, "licence": "MIT"}, "permissions": ["none"],
                "scripts": [{"file": "scripts/count.py", "sha256": h(SCRIPT), "inputs": {}, "timeout": 60}],
                "packages": [], "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def test_publish_opens_a_pull_request_on_a_new_branch_never_pushes_main(tmp_path):
    gh = FakeGitHub(library_files(entry()))
    library = make(tmp_path, gh)
    folder = _skill_folder(tmp_path)
    url = library.publish(folder)
    assert url == f"https://github.com/{REPO}/pull/7"
    writes = [r for r in gh.calls if r.method != "GET"]
    new_ref = next(r for r in writes if r.url.path.endswith("/git/refs"))
    branch = json.loads(new_ref.content)["ref"]
    assert branch.startswith("refs/heads/skill/repo-owner-tool-1.0.0")
    for r in writes:
        assert "/refs/heads/main" not in r.url.path and "/contents/" not in r.url.path   # never a direct push
        if r.url.path.endswith("/git/refs") or "/git/refs/heads/" in r.url.path:
            assert "main" not in r.url.path.rsplit("/heads/", 1)[-1]
    commits = [json.loads(r.content) for r in writes if r.url.path.endswith("/git/commits")]
    assert len(commits) == 2                                           # files, then the index entry
    pr = json.loads(next(r for r in writes if r.url.path.endswith("/pulls")).content)
    assert pr["base"] == "main" and pr["head"] == branch.removeprefix("refs/heads/")
    index_blob = next(b for b in gh.blobs.values() if b.startswith(b"{") and b'"skills"' in b)
    index = json.loads(index_blob)
    new = next(e for e in index["skills"] if e["name"] == "repo-owner-tool")
    on_disk = {p.relative_to(folder).as_posix(): h(p.read_bytes()) for p in folder.rglob("*") if p.is_file()}
    assert new["files"] == on_disk and new["version"] == "1.0.0" and new["commit"] == "commit005"   # 3 blobs and a tree come first; commit 1 = files
    assert [e["name"] for e in index["skills"]] == ["word-tools", "repo-owner-tool"]     # the old entry stays
    tree_paths = {e["path"] for e in gh.trees[0]["tree"]}
    assert tree_paths == {f"repo-owner-tool/{p}" for p in on_disk}


def test_publish_refuses_a_skill_whose_files_dont_match_its_manifest(tmp_path):
    gh = FakeGitHub(library_files(entry()))
    folder = _skill_folder(tmp_path)
    (folder / "scripts" / "count.py").write_text("print('changed')\n", encoding="utf-8")
    with pytest.raises(lib.LibraryError, match="manifest"):
        make(tmp_path, gh).publish(folder)
    assert [r for r in gh.calls if r.method != "GET"] == []


# -- wiring ----------------------------------------------------------------------------------------------------

def test_the_new_key_and_the_app_wiring(app_client):
    from prometheus.config import KEY_NAMES

    assert KEY_NAMES[lib.TOKEN_NAME] == "GitHub skills library"
    _, services = app_client()
    assert isinstance(services.skills.library, lib.Library)
    assert lib.PUBLISH_KIND in services.approvals._handlers


def test_library_repo_is_next_to_the_owners_repo():
    class S:
        github_repo = "Reevias/Prometheus-"
    assert lib.library_repo(S()) == "Reevias/prometheus-skills"
