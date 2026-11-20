"""M3 D6 review (20 Nov): bugs found while checking Phases A to C. Each test failed before its fix."""
from __future__ import annotations

import subprocess
import sys

import pytest

from prometheus import sandbox
from prometheus.launcher import apply_update, review_update
from test_v19_updates import git


def _repos(tmp_path, filename):
    origin, mine = tmp_path / "origin", tmp_path / "mine"
    origin.mkdir()
    git(origin, "init", "-q", "-b", "v3")
    git(origin, "config", "user.name", "Test")
    git(origin, "config", "user.email", "test@example.invalid")
    (origin / "pyproject.toml").write_text("# version one")
    (origin / "notes.txt").write_text("one")
    git(origin, "add", ".")
    git(origin, "commit", "-qm", "base")
    subprocess.run(["git", "clone", "-q", str(origin), str(mine)], check=True)
    _commit(origin, filename, "# changed", "review me")
    return origin, mine


def _commit(origin, filename, text, message):
    dest = origin / filename
    dest.parent.mkdir(exist_ok=True)
    dest.write_text(text)
    git(origin, "add", ".")
    git(origin, "commit", "-qm", message)


def _head(path):
    return subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_approved_dependency_update_installs_its_packages(tmp_path):
    """Bug: the reviewed update only pulled the code, so a new package was missing after the automatic restart."""
    _, mine = _repos(tmp_path, "pyproject.toml")
    review = review_update(mine)
    assert review["sensitive"]
    installed = []
    apply_update(mine, review, install=lambda path: installed.append((path, (path / "pyproject.toml").read_text())))
    assert installed == [(mine, "# changed")]


def test_failed_package_install_puts_the_old_code_back(tmp_path):
    _, mine = _repos(tmp_path, "pyproject.toml")
    review = review_update(mine)

    def broken(path):
        raise RuntimeError("pip failed")

    with pytest.raises(RuntimeError, match="old version"):
        apply_update(mine, review, install=broken)
    assert _head(mine) == review["base"] and (mine / "pyproject.toml").read_text() == "# version one"


def test_code_only_update_installs_nothing(tmp_path):
    _, mine = _repos(tmp_path, "notes.txt")         # an existing top-level path: not sensitive
    review = review_update(mine)
    assert review and not review["sensitive"]
    calls = []
    assert "Updated" in apply_update(mine, review, install=calls.append)
    assert calls == []


def test_newer_commits_replace_an_older_update_request(tmp_path, app_client):
    """Bug: an older update request stayed pending next to the new one and could only fail when approved."""
    from prometheus.autoupdate import AutoUpdater

    origin, mine = _repos(tmp_path, "pyproject.toml")
    _, services = app_client()
    updater = AutoUpdater(services, mine)
    updater.check_once()
    first, = services.store.approvals()
    _commit(origin, "pyproject.toml", "# changed again", "second change")
    updater.check_once()
    pending = services.store.approvals()
    assert len(pending) == 1 and pending[0].id != first.id and "second change" in pending[0].summary
    assert services.store.approval(first.id).status == "rejected"


@pytest.mark.skipif(sys.platform == "win32", reason="runs the guard directly; the Windows venv job covers Windows")
def test_guard_reads_pth_files_of_the_given_package_folders(tmp_path):
    """Bug: the guard started with -S, so packages that need a .pth file (pywin32...) didn't import."""
    packages = tmp_path / "site"
    (packages / "extra").mkdir(parents=True)
    (packages / "extra" / "pth_only_module.py").write_text("VALUE = 42\n")
    (packages / "demo.pth").write_text("extra\n")
    folder = sandbox.new_folder(tmp_path / "runs")
    (folder / sandbox.SCRIPT_NAME).write_text("import pth_only_module\nprint(pth_only_module.VALUE)\n")
    (folder / sandbox.GUARD_NAME).write_text(sandbox.GUARD)
    proc = subprocess.run([sys.executable, "-I", "-S", str(folder / sandbox.GUARD_NAME), str(folder), "0",
                           str(tmp_path / "private"), str(packages)], capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.strip() == "42", proc.stdout + proc.stderr
