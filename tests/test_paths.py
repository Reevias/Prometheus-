import os
import sys

import pytest

from prometheus.safety.paths import PathBlocked, PathPolicy


@pytest.fixture
def setup(tmp_path):
    home = tmp_path / "home"
    data = home / "AppData" / "Local" / "Prometheus"
    code = home / "code" / "prometheus"
    for d in (home / "Downloads", home / ".ssh", data, code):
        d.mkdir(parents=True)
    return home, PathPolicy(home=home, data_dir=data, code_dir=code)


def test_outside_home_blocked(setup, tmp_path):
    _, policy = setup
    outside = tmp_path / "elsewhere.txt"
    outside.write_text("x")
    with pytest.raises(PathBlocked):
        policy.check_read(outside)


def test_ssh_blocked(setup):
    home, policy = setup
    with pytest.raises(PathBlocked):
        policy.check_read(home / ".ssh" / "id_ed25519")


def test_code_dir_blocked(setup):
    home, policy = setup
    with pytest.raises(PathBlocked):
        policy.check_read(home / "code" / "prometheus" / "safety.py")


def test_appdata_blocked_but_data_dir_readable(setup):
    home, policy = setup
    with pytest.raises(PathBlocked):
        policy.check_read(home / "AppData" / "Roaming" / "Bitwarden" / "data.json")
    assert policy.check_read(home / "AppData" / "Local" / "Prometheus" / "memory.db")


def test_keepass_folder_blocked(setup):
    home, policy = setup
    with pytest.raises(PathBlocked):
        policy.check_read(home / "Documents" / "KeePassXC" / "vault.kdbx")


def test_change_outside_task_root_blocked(setup):
    home, policy = setup
    (home / "Documents").mkdir()
    with pytest.raises(PathBlocked):
        policy.check_change(home / "Documents" / "a.txt", task_root=home / "Downloads")
    assert policy.check_change(home / "Downloads" / "a.txt", task_root=home / "Downloads")


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_symlink_change_blocked(setup):
    home, policy = setup
    target = home / "Downloads" / "real.txt"
    target.write_text("x")
    link = home / "Downloads" / "link.txt"
    link.symlink_to(target)
    with pytest.raises(PathBlocked):
        policy.check_change(link, task_root=home / "Downloads")


def test_dotdot_escape_blocked(setup):
    home, policy = setup
    with pytest.raises(PathBlocked):
        policy.check_read(home / "Downloads" / ".." / ".." / "x")


def test_spaces_and_unicode_ok(setup):
    home, policy = setup
    p = home / "My Files" / "résumé.pdf"
    assert policy.check_read(p).name == "résumé.pdf"


def test_case_insensitive_on_windows(setup, monkeypatch):
    home, policy = setup
    monkeypatch.setattr(os.path, "normcase", str.lower)
    with pytest.raises(PathBlocked):
        policy.check_read(home / ".SSH" / "id")
    assert policy.is_blocked(home / ".SSH")


def test_secret_files_in_data_dir_blocked(setup):
    home, policy = setup
    data = home / "AppData" / "Local" / "Prometheus"
    for name in (".env", "settings.json"):
        with pytest.raises(PathBlocked):
            policy.check_read(data / name)
