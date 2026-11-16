"""Security audit (16 Nov): file copy and rename. Each test failed before its fix."""
from __future__ import annotations

from prometheus.actions.files import FileActions
from prometheus.actions.journal import Journal
from prometheus.safety.paths import PathPolicy


def mk(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    ext = tmp_path / "ext"
    ext.mkdir()
    data = home / "AppData" / "Local" / "Prometheus"
    data.mkdir(parents=True)
    (data / ".env").write_text("KEY=secret")
    (home / ".ssh").mkdir()
    (home / ".ssh" / "id_rsa").write_text("PRIVATE")
    (home / "Documents").mkdir()
    (home / "Documents" / "notes.txt").write_text("fine")
    pol = PathPolicy(home, data, tmp_path / "code", extra_roots=[ext])
    return home, ext, pol, FileActions(pol, Journal(tmp_path / "j"), trash=lambda p: None)


def test_copying_a_folder_never_copies_protected_files_out(tmp_path):
    home, ext, pol, fa = mk(tmp_path)
    run = fa.journal.start(ext)
    fa.copy(run, home, ext, ext)
    assert not list(ext.rglob("id_rsa")) and not list(ext.rglob(".env"))
    assert list(ext.rglob("notes.txt"))              # the rest of the folder is still copied


def test_rename_cannot_make_odd_names(tmp_path):
    home, ext, pol, fa = mk(tmp_path)
    f = home / "Documents" / "notes.txt"
    run = fa.journal.start(home)
    for bad in ("a.txt:evil", "a.exe::$DATA", "CON", "x.exe.", "x.exe ", "nul.txt", ".git"):
        r = fa.rename(run, f, bad, home)
        assert r.status == "skipped", (bad, r)
        assert f.exists()
