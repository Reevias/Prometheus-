import os
import shutil

import pytest

from prometheus.actions.files import FileActions, stamp
from prometheus.actions.journal import Journal
from prometheus.safety.paths import PathPolicy


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    root = home / "Downloads"
    root.mkdir(parents=True)
    data = tmp_path / "data"
    trashed = []
    policy = PathPolicy(home=home, data_dir=data, code_dir=tmp_path / "code")
    journal = Journal(data / "journals")
    actions = FileActions(policy, journal, trash=lambda p: (trashed.append(p), os.remove(p)))
    return root, journal, actions, trashed


def listing(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_move_and_undo_restores(env):
    root, journal, actions, _ = env
    (root / "a.pdf").write_text("x")
    before = listing(root)
    run = journal.start(root)
    res = actions.move(run, root / "a.pdf", root / "Documents" / "Tax", root)
    assert res.status == "done"
    assert (root / "Documents" / "Tax" / "a.pdf").exists()
    journal.finish(run)
    report = actions.undo_run(run)
    assert report.restored == 1
    assert listing(root) == before


def test_name_collision_gets_suffix(env):
    root, journal, actions, _ = env
    (root / "Docs").mkdir()
    (root / "Docs" / "report.pdf").write_text("old")
    (root / "report.pdf").write_text("new")
    run = journal.start(root)
    res = actions.move(run, root / "report.pdf", root / "Docs", root)
    assert res.new_path.name == "report (1).pdf"
    assert (root / "Docs" / "report.pdf").read_text() == "old"


def test_changed_since_scan_skipped(env):
    root, journal, actions, _ = env
    f = root / "a.txt"
    f.write_text("x")
    expected = stamp(f)
    f.write_text("changed content")
    run = journal.start(root)
    res = actions.move(run, f, root / "Other", root, expected=expected)
    assert res.status == "skipped"
    assert f.exists()


def test_locked_file_skipped(env, monkeypatch):
    root, journal, actions, _ = env
    (root / "a.txt").write_text("x")
    (root / "b.txt").write_text("y")
    run = journal.start(root)

    def boom(*args, **kwargs):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(shutil, "move", boom)
    res = actions.move(run, root / "a.txt", root / "Other", root)
    assert res.status == "skipped"
    assert "in use" in res.detail
    assert journal.load(run)["entries"] == []


def test_recycle_uses_trash_and_records(env):
    root, journal, actions, trashed = env
    f = root / "old.exe"
    f.write_text("x")
    run = journal.start(root)
    res = actions.recycle(run, f, root)
    assert res.status == "done"
    assert trashed == [str(f.resolve())]
    journal.finish(run)
    report = actions.undo_run(run)
    assert any("Recycle Bin" in p for p in report.problems)


def test_rename_and_undo_entry(env):
    root, journal, actions, _ = env
    (root / "IMG_1.jpg").write_text("x")
    (root / "b.txt").write_text("y")
    run = journal.start(root)
    actions.rename(run, root / "IMG_1.jpg", "beach.jpg", root)
    actions.move(run, root / "b.txt", root / "Text", root)
    journal.finish(run)
    report = actions.undo_entry(run, 0)
    assert report.restored == 1
    assert (root / "IMG_1.jpg").exists()
    assert (root / "Text" / "b.txt").exists()


def test_journal_survives_crash(env):
    root, journal, actions, _ = env
    (root / "a.txt").write_text("x")
    (root / "b.txt").write_text("y")
    run = journal.start(root)
    actions.move(run, root / "a.txt", root / "T", root)
    actions.move(run, root / "b.txt", root / "T", root)
    # no finish(): simulate a crash, then a fresh Journal object reads it back
    fresh = Journal(journal.folder)
    assert len(fresh.load(run)["entries"]) == 2
    report = FileActions(actions.policy, fresh).undo_run(run)
    assert report.restored == 2


def test_undo_removes_created_empty_dirs_only(env):
    root, journal, actions, _ = env
    (root / "a.txt").write_text("x")
    run = journal.start(root)
    actions.move(run, root / "a.txt", root / "New" / "Deep", root)
    (root / "New" / "keep.txt").write_text("user file")
    journal.finish(run)
    actions.undo_run(run)
    assert not (root / "New" / "Deep").exists()
    assert (root / "New" / "keep.txt").exists()


def test_blocked_path_not_moved(env, tmp_path):
    root, journal, actions, _ = env
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    run = journal.start(root)
    res = actions.move(run, outside, root / "X", root)
    assert res.status == "skipped"
    assert outside.exists()


def test_runs_listing_ignores_damaged(env):
    root, journal, actions, _ = env
    run = journal.start(root)
    (journal.folder / "broken.json").write_text("{not json")
    assert [r["id"] for r in journal.runs()] == [run]
