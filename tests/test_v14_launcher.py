import os
import subprocess

import pytest

from prometheus import launcher
from prometheus.cli import main


def test_old_copy_detected_by_remote_or_folder(tmp_path):
    assert launcher.is_old_copy(tmp_path, "https://github.com/Reevias/Claude-testing-.git")
    assert launcher.is_old_copy(tmp_path / "Claude-testing-" / "prometheus", "")
    assert not launcher.is_old_copy(tmp_path, "https://github.com/Reevias/Prometheus-.git")
    assert "OLD copy" in launcher.old_copy_warning(tmp_path, "git@github.com:Reevias/Claude-testing-.git")
    assert launcher.old_copy_warning(tmp_path, "https://github.com/Reevias/Prometheus-.git") == ""


def test_explicit_update_reviews_then_applies_that_revision(tmp_path, monkeypatch):
    review = {"branch": "v3", "base": "a" * 40, "target": "b" * 40}
    calls = []
    monkeypatch.setattr(launcher, "review_update", lambda path, run: review)
    monkeypatch.setattr(launcher, "apply_update", lambda path, item, run: calls.append((path, item)) or "Updated")
    assert launcher.update(tmp_path) == "Updated" and calls == [(tmp_path, review)]



def test_failed_pull_stops_before_install(tmp_path):
    (tmp_path / ".git").mkdir()
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "local changes")

    with pytest.raises(RuntimeError, match="did not go through"):
        launcher.update(tmp_path, run)
    assert len(calls) == 1


def test_shortcut_paths_travel_in_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    seen = []

    def run(argv, env=None, **kw):
        seen.append((argv, env))
        return subprocess.CompletedProcess(argv, 0, "", "")

    made = launcher.make_shortcuts(run, windows=True)
    assert [p.name for p in made] == ["Prometheus.lnk", "Prometheus.lnk"]
    for argv, env in seen:
        assert argv[-1] == launcher.SHORTCUT_PS                     # fixed command text
        assert env["PROM_LINK"].endswith("Prometheus.lnk") and env["PROM_DIR"] == str(launcher.CODE_DIR)


@pytest.mark.skipif(os.name == "nt", reason="checks the non-Windows message")
def test_shortcut_command_explains_off_windows(capsys):
    assert main(["shortcut"]) == 1
    assert "Windows only" in capsys.readouterr().out


def test_doctor_prints_version(tmp_home, capsys):
    main(["doctor"])
    assert "Prometheus:" in capsys.readouterr().out
