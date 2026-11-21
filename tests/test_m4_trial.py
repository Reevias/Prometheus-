"""M4.6 Sandbox test-run before switching on: every script in a skill's manifest runs once in the sandbox with its
sample inputs; a skill switches on only when all of them pass."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from conftest import login

from prometheus import sandbox
from prometheus.repos import trial as trial_mod
from prometheus.repos.trial import trial

SKILL_MD = "---\nname: {name}\ndescription: A test skill that is long enough to be valid.\n---\n\n# Test\n"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_skill(root: Path, name="repo-test-tool", scripts=None, permissions=("none",), packages=()):
    """A skill folder with a SKILL.md, scripts and a manifest.json laid out as docs/m4/MANIFEST.md says.
    scripts: {"scripts/x.py": (code, inputs, timeout)}."""
    folder = root / name
    files = {}
    (folder / "scripts").mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(SKILL_MD.format(name=name), encoding="utf-8")
    files["SKILL.md"] = _sha((folder / "SKILL.md").read_bytes())
    entries = []
    for rel, (code, inputs, timeout) in (scripts or {}).items():
        (folder / rel).write_text(code, encoding="utf-8")
        files[rel] = _sha((folder / rel).read_bytes())
        entries.append({"file": rel, "sha256": files[rel], "inputs": inputs, "timeout": timeout})
    manifest = {"name": name, "version": "1.0.0", "description": "A test skill.",
                "source": {"repo": "owner/tool", "commit": "b" * 40, "licence": "MIT"},
                "permissions": list(permissions), "scripts": entries, "packages": list(packages), "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return folder


def _rewrite_manifest_sha(folder: Path, rel: str):
    path = folder / "manifest.json"
    m = json.loads(path.read_text(encoding="utf-8"))
    sha = _sha((folder / rel).read_bytes())
    m["files"][rel] = sha
    for s in m["scripts"]:
        if s["file"] == rel:
            s["sha256"] = sha
    path.write_text(json.dumps(m), encoding="utf-8")


ANSWER = ("import json\nwith open('inputs.json', encoding='utf-8') as f:\n    data = json.load(f)\n"
          "print(json.dumps({'double': data['x'] * 2}))\n")


def test_a_script_that_prints_an_answer_passes(tmp_home, tmp_path):
    folder = make_skill(tmp_path, scripts={"scripts/a.py": (ANSWER, {"x": 21}, 30)})
    result = trial(folder)
    assert result.passed and result.reason == ""
    assert result.scripts[0].status == "passed" and '"double": 42' in result.scripts[0].details


def test_a_script_that_raises_fails_with_the_error_in_words(tmp_home, tmp_path):
    folder = make_skill(tmp_path, scripts={"scripts/a.py": ("raise ValueError('boom')\n", {}, 30)})
    result = trial(folder)
    assert not result.passed
    assert result.reason.startswith("The script stopped with an error:") and "ValueError: boom" in result.reason
    assert "Traceback" not in result.reason and "Traceback" in result.scripts[0].details     # the raw text is behind details


def test_a_script_that_sleeps_past_its_timeout_fails(tmp_home, tmp_path):
    folder = make_skill(tmp_path, scripts={"scripts/a.py": ("import time\ntime.sleep(20)\n", {}, 1)})
    result = trial(folder)
    assert not result.passed and "took longer than 1 second" in result.reason


def test_a_script_that_uses_the_network_without_permission_fails(tmp_home, tmp_path):
    code = "import socket\nsocket.create_connection(('example.com', 80), timeout=2)\n"
    folder = make_skill(tmp_path, scripts={"scripts/a.py": (code, {}, 30)}, permissions=("none",))
    result = trial(folder)
    assert not result.passed and "internet without permission" in result.reason
    assert "Prometheus sandbox" in result.scripts[0].details                                  # the sandbox's own message


def test_a_script_with_the_network_permission_is_not_run(tmp_home, tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(sandbox, "run_locked", lambda *a, **k: ran.append(a) or None)
    folder = make_skill(tmp_path, scripts={"scripts/a.py": ("import socket\n", {}, 30)}, permissions=("network",))
    result = trial(folder)
    assert ran == [] and result.passed
    assert result.scripts[0].status == "skipped" and "needs the internet" in result.scripts[0].reason
    assert "you'll be asked at first real use" in result.scripts[0].reason


def test_a_file_that_doesnt_match_the_manifest_fails_before_anything_runs(tmp_home, tmp_path, monkeypatch):
    ran = []
    monkeypatch.setattr(sandbox, "run_locked", lambda *a, **k: ran.append(a) or None)
    folder = make_skill(tmp_path, scripts={"scripts/a.py": (ANSWER, {"x": 1}, 30)})
    (folder / "scripts" / "a.py").write_text(ANSWER + "# changed\n", encoding="utf-8")
    result = trial(folder)
    assert ran == [] and not result.passed
    assert "changed since" in result.reason and "scripts/a.py" in result.reason
    other = make_skill(tmp_path, name="repo-other", scripts={"scripts/a.py": (ANSWER, {"x": 1}, 30)})
    (other / "SKILL.md").write_text("tampered", encoding="utf-8")                             # any listed file counts
    assert not trial(other).passed and ran == []


def test_editing_a_script_after_it_passed_needs_a_new_trial(tmp_home, tmp_path):
    folder = make_skill(tmp_path, scripts={"scripts/a.py": (ANSWER, {"x": 1}, 30)})
    first = trial(folder)
    assert first.passed and first.still_valid(folder)
    (folder / "scripts" / "a.py").write_text(ANSWER + "# edited\n", encoding="utf-8")
    assert not first.still_valid(folder)                                                      # the old pass no longer counts
    assert not trial(folder).passed                                                           # and the manifest no longer matches
    _rewrite_manifest_sha(folder, "scripts/a.py")                                             # the new version, made properly
    second = trial(folder)
    assert second.passed and second.still_valid(folder) and not first.still_valid(folder)


def test_a_missing_package_keeps_the_skill_off(tmp_home, tmp_path):
    folder = make_skill(tmp_path, scripts={"scripts/a.py": ("print(1)\n", {}, 30)}, packages=("no_such_package_xyz",))
    result = trial(folder)
    assert not result.passed and "no_such_package_xyz" in result.reason and "isn't installed" in result.reason


def test_a_guide_only_skill_passes_with_nothing_to_run(tmp_home, tmp_path):
    assert trial(make_skill(tmp_path, scripts={})).passed


def _skills_folder(services):
    folder = Path(services.skills.user)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def test_switching_on_runs_the_trial_first(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    good = make_skill(_skills_folder(services), name="repo-good", scripts={"scripts/a.py": (ANSWER, {"x": 2}, 30)})
    bad = make_skill(_skills_folder(services), name="repo-bad", scripts={"scripts/a.py": ("raise KeyError('k')\n", {}, 30)})
    from prometheus.features import set_skill_on, skill_on

    set_skill_on(services.settings, "repo-bad", False)                                       # (as Accept leaves them)
    set_skill_on(services.settings, "repo-good", False)
    r = client.post("/api/v3/skills/repo-bad/switch", data={"on": "1"}, headers=h)
    assert r.status_code == 409 and "stopped with an error" in r.json()["error"]
    assert not skill_on(services.settings, "repo-bad")                                       # it stayed off
    r = client.post("/api/v3/skills/repo-good/switch", data={"on": "1"}, headers=h)
    assert r.status_code == 200 and r.json()["on"] is True and skill_on(services.settings, "repo-good")
    assert client.post("/api/v3/skills/repo-bad/switch", data={"on": ""}, headers=h).status_code == 200   # off is always allowed
    assert good.is_dir() and bad.is_dir()


def test_candidates_show_the_trial_result_in_words(app_client):
    from prometheus.repo_of_day import add_candidate, set_candidate_status, set_candidate_suggestion

    client, services = app_client()
    login(client)
    cand = add_candidate(services.store, "owner/tool")
    set_candidate_suggestion(services.store, cand["id"], "repo-owner-tool", "Ideas from owner/tool", kind="draft")
    set_candidate_status(services.store, cand["id"], "accepted")
    folder = make_skill(_skills_folder(services), name="repo-owner-tool",
                        scripts={"scripts/a.py": ("raise ValueError('nope')\n", {}, 30)})
    trial_mod.save_result(services.store, "repo-owner-tool", trial(folder))
    page = client.get("/v3/candidates").text
    assert "Trial run" in page and "The script stopped with an error" in page and "Show details" in page
    assert "<script>" not in page.split("Trial run", 1)[1][:600]


def _accept_setup(services, name, script):
    """A downloaded repo (profile in the quarantine), a drafted skill in skill_drafts, and a candidate pointing at both."""
    import time

    from prometheus.config import data_dir
    from prometheus.repo_of_day import add_candidate, set_candidate_suggestion
    from prometheus.repos.profile import RepoProfile, save

    folder = data_dir() / "quarantine" / "owner__tool__abc1234"
    folder.mkdir(parents=True, exist_ok=True)
    save(RepoProfile(repo="owner/tool", commit="a" * 40, url="https://github.com/owner/tool", folder=str(folder),
                     verdict="clean", licence="MIT", made=time.time()))
    make_skill(data_dir() / "skill_drafts", name=name, scripts={"scripts/a.py": (script, {"x": 3}, 30)})
    cand = add_candidate(services.store, "owner/tool", folder=str(folder))
    set_candidate_suggestion(services.store, cand["id"], name, "Ideas from owner/tool", kind="draft")
    return cand


def test_accept_tests_the_new_skill_and_switches_it_on_only_if_it_passed(app_client):
    from prometheus.features import skill_on

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    cand = _accept_setup(services, "repo-owner-tool", ANSWER)
    r = client.post(f"/api/v3/candidates/{cand['id']}/accept", headers=h)
    assert r.status_code == 200 and "trial run in the sandbox passed" in r.json()["message"]
    assert skill_on(services.settings, "repo-owner-tool")
    assert "Passed in the sandbox." in client.get("/v3/candidates").text


def test_accept_leaves_a_failing_skill_off_and_says_why(app_client):
    from prometheus.features import skill_on

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    cand = _accept_setup(services, "repo-owner-tool", "raise RuntimeError('broken script')\n")
    r = client.post(f"/api/v3/candidates/{cand['id']}/accept", headers=h)
    assert r.status_code == 200 and "didn't pass, so it stays off" in r.json()["message"]
    assert not skill_on(services.settings, "repo-owner-tool")
    page = client.get("/v3/candidates").text
    assert "The skill stays off." in page and "RuntimeError: broken script" in page
