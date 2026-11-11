import subprocess
from types import SimpleNamespace

from prometheus.autoupdate import AutoUpdater
from prometheus.launcher import update_report, version


def git(path, *args):
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def test_doctor_report_current_and_latest(tmp_path):
    origin, mine = tmp_path / "origin", tmp_path / "mine"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "main")
    git(origin, "config", "user.email", "t@t")
    git(origin, "config", "user.name", "t")
    (origin / "pyproject.toml").write_text('[project]\nversion = "2.1.0"\n')
    (origin / "CHANGELOG.md").write_text("# Changelog\n\n## 2.1\n- Old thing\n")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "one")
    subprocess.run(["git", "clone", "-q", str(origin), str(mine)], check=True)
    assert version(mine) == "2.1.0"
    rep = update_report(mine)
    assert rep["behind"] == 0 and rep["latest"] == "2.1.0" and not rep["error"]
    (origin / "pyproject.toml").write_text('[project]\nversion = "2.2.0"\n')
    (origin / "CHANGELOG.md").write_text("# Changelog\n\n## 2.2\n- Phone control\n\n## 2.1\n- Old thing\n")
    git(origin, "commit", "-qam", "two")
    rep = update_report(mine)
    assert rep["behind"] == 1 and rep["latest"] == "2.2.0" and rep["news"] == ["Phone control"]
    assert update_report(tmp_path / "nothing")["error"]


def test_restart_waits_until_idle_and_reopens_the_same_way(app_client):
    _, services = app_client()
    up = AutoUpdater(services)
    services.running[5] = True
    assert up.busy() == "a task"
    services.running.clear()
    assert up.busy() == ""
    spawned, left = [], []
    up.restart_when_idle(mode="app", spawn=lambda cmd, **kw: spawned.append(cmd), leave=left.append)
    assert spawned[0][-1] == "app" and left == [0]
    assert services.store.kv_get("just_updated")
    from prometheus.server import _say_whats_new

    _say_whats_new(services)
    chat = services.watchers.alerts_chat()
    assert "updated itself to version" in services.store.messages(chat)[-1].text
    assert not services.store.kv_get("just_updated")                     # said once
