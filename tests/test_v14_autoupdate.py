import subprocess
from types import SimpleNamespace

from prometheus.autoupdate import AutoUpdater


def fake_services(running=False, on=True):
    audits = []
    return SimpleNamespace(settings=SimpleNamespace(auto_update=on), running={1: True} if running else {},
                           store=SimpleNamespace(audit=lambda *a: audits.append(a)), voice=None, audits=audits)


def git(behind):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        out = str(behind) if "rev-list" in argv else ""
        return subprocess.CompletedProcess(argv, 0, out, "")
    return run, calls


def test_installs_when_github_has_a_newer_version(tmp_path):
    (tmp_path / ".git").mkdir()
    run, calls = git(3)
    updates = []
    up = AutoUpdater(fake_services(), tmp_path, run=run, updater=updates.append)
    assert "restarts by itself" in up.check_once() and updates == [tmp_path]
    assert any("fetch" in c for c in calls) and up.updated


def test_does_nothing_when_up_to_date_busy_off_or_not_a_checkout(tmp_path):
    (tmp_path / ".git").mkdir()
    updates = []
    run, _ = git(0)
    assert AutoUpdater(fake_services(), tmp_path, run=run, updater=updates.append).check_once() == ""
    run, calls = git(2)
    assert AutoUpdater(fake_services(running=True), tmp_path, run=run, updater=updates.append).check_once() == ""
    assert calls == []                                   # not even checked while a task runs
    assert AutoUpdater(fake_services(on=False), tmp_path, run=run, updater=updates.append).check_once() == ""
    assert AutoUpdater(fake_services(), tmp_path / "nope", run=run, updater=updates.append).check_once() == ""
    assert updates == []


def test_offline_or_failed_update_is_quiet(tmp_path):
    (tmp_path / ".git").mkdir()

    def offline(argv, **kw):
        return subprocess.CompletedProcess(argv, 1, "", "could not resolve host")
    up = AutoUpdater(fake_services(), tmp_path, run=offline, updater=lambda d: None)
    assert up.check_once() == "" and "resolve" in up.last_error


def test_voice_offers_heart_and_fable(app_client):
    from prometheus.voice.engines import DEFAULT_VOICE, VOICES
    from tests.conftest import login

    assert [v for v, _ in VOICES] == ["af_heart", "bm_fable"] and DEFAULT_VOICE == "af_heart"
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    assert client.post("/settings/voice/name", data={"name": "bm_fable"}, headers=h).status_code == 200
    assert services.settings.voice_name == "bm_fable"
    assert client.post("/settings/voice/name", data={"name": "am_michael"}, headers=h).status_code == 400
    assert client.post("/settings/autoupdate", data={}, headers=h).status_code == 200
    assert services.settings.auto_update is False


def test_restarts_when_the_code_was_updated_from_outside(tmp_path):
    (tmp_path / ".git").mkdir()
    heads = iter(["aaa", "aaa", "bbb"])

    def run(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, next(heads) if "rev-parse" in argv else "0", "")

    up = AutoUpdater(fake_services(), tmp_path, run=run, updater=lambda d: None)
    up.started_at = up.head()
    assert not up.code_changed()           # same code: nothing to do
    assert up.code_changed()               # the installer pulled new code while this one runs
