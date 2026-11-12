"""M0/M1 checks that need native Windows (NTFS, junctions, sharing modes, short names), plus recovery after a real
process kill (runs everywhere). Windows-only tests are skipped elsewhere: they are only verified on Windows runs."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest

import prometheus.actions.atomic_fs as afs_mod
from prometheus.actions.atomic_fs import AtomicFiles, PathSecurityError, TransactionError
from prometheus.safety.paths import PathBlocked, PathPolicy

WIN = sys.platform == "win32"
windows_only = pytest.mark.skipif(not WIN, reason="needs native Windows")


@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    data = home / "AppData" / "Local" / "Prometheus"
    code = home / "code" / "prometheus"
    project = home / "projects" / "app"
    for d in (data, code, project):
        d.mkdir(parents=True, exist_ok=True)
    policy = PathPolicy(home=home, data_dir=data, code_dir=code)
    return home, project, data, policy, AtomicFiles(policy=policy, backup_dir=data / "backups")


def junction(link: Path, target: Path) -> None:
    subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)


# ---- Windows path handling -----------------------------------------------------------------------------------

@windows_only
@pytest.mark.parametrize("name", ["AppData", "appdata", "APPDATA", "AppData.", "AppData ", "AppData. ."])
def test_blocked_folder_case_and_trailing_dots(env, name):
    home, _, _, policy, _ = env
    with pytest.raises(PathBlocked):
        policy.check_read(str(home) + "\\" + name + "\\Local\\Prometheus\\x.txt")


@windows_only
def test_blocked_folder_by_short_name(env):
    home, _, _, policy, _ = env
    k32 = ctypes.windll.kernel32
    buf = ctypes.create_unicode_buffer(1024)
    n = k32.GetShortPathNameW(str(home / "AppData"), buf, 1024)
    short = buf.value if n else ""
    if not short or short.lower().endswith("appdata"):
        pytest.skip("8.3 short names are turned off on this volume")
    with pytest.raises(PathBlocked):
        policy.check_read(short + "\\Local")


@windows_only
@pytest.mark.parametrize("raw", [r"\\localhost\c$\Windows", "//localhost/c$/x", r"\\?\C:\x", r"\\.\PhysicalDrive0",
                                 r"\\?\UNC\server\share", r"C:\x\file.txt:secret", "NUL", r"x\COM1.txt",
                                 "LPT\u00b2", "CONOUT$"])
def test_windows_special_paths_refused(env, raw):
    _, _, _, policy, _ = env
    with pytest.raises(PathBlocked):
        policy.check_read(raw)


@windows_only
def test_other_drive_letter_and_drive_relative_paths_refused(env):
    home, _, _, policy, _ = env
    drive = Path(str(home)).drive
    other = "Z:" if drive.upper() != "Z:" else "Y:"
    for raw in (other + r"\x.txt", other + "x.txt", r"C:\Windows\win.ini"):
        with pytest.raises(PathBlocked):
            policy.check_read(raw)
    # the same folder written with another letter case is the same folder
    assert policy.check_read(str(home / "projects").upper())


@windows_only
def test_junction_inside_project_refused_for_changes(env, tmp_path):
    _, project, _, policy, afs = env
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "f.txt").write_text("x", encoding="utf-8")
    junction(project / "j", outside)
    with pytest.raises((PathBlocked, PathSecurityError)):
        afs.edit_exact(project / "j" / "f.txt", "x", "y", root=project)
    assert (outside / "f.txt").read_text(encoding="utf-8") == "x"


@windows_only
def test_rollback_cannot_escape_through_swapped_junction(env, tmp_path):
    _, project, _, _, afs = env
    sub = project / "sub"
    sub.mkdir()
    target = sub / "t.txt"
    target.write_text("one", encoding="utf-8")
    res = afs.edit_exact(target, "one", "two", root=project)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "t.txt").write_text("two", encoding="utf-8")   # same content, so only the path checks can stop it
    os.rename(sub, project / "sub_moved")
    junction(sub, outside)
    with pytest.raises((TransactionError, PathBlocked, PathSecurityError)):
        afs.rollback_transaction(res.transaction_id)
    assert (outside / "t.txt").read_text(encoding="utf-8") == "two"
    out = afs.recover_interrupted_transactions()      # journal paths are re-checked at recovery too
    assert (outside / "t.txt").read_text(encoding="utf-8") == "two"


# ---- Windows replace: sharing violations, attributes ---------------------------------------------------------

def _open_without_share_delete(path: Path):
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p,
                                ctypes.c_ulong, ctypes.c_ulong, ctypes.c_void_p]
    h = k32.CreateFileW(str(path), 0x80000000, 0x1 | 0x2, None, 3, 0, None)   # GENERIC_READ, share read/write only
    assert h and h != ctypes.c_void_p(-1).value
    return k32, h


@windows_only
def test_edit_fails_safely_while_file_is_open_without_share_delete(env):
    _, project, _, _, afs = env
    target = project / "busy.txt"
    target.write_text("one", encoding="utf-8")
    k32, h = _open_without_share_delete(target)
    try:
        with pytest.raises(Exception):
            afs.edit_exact(target, "one", "two", root=project)
    finally:
        k32.CloseHandle(ctypes.c_void_p(h))
    assert target.read_text(encoding="utf-8") == "one"
    assert not [p for p in project.iterdir() if ".tmp_" in p.name]       # staged file cleaned up


@windows_only
def test_edit_succeeds_when_lock_is_released_during_retries(env):
    _, project, _, _, afs = env
    target = project / "brief.txt"
    target.write_text("one", encoding="utf-8")
    k32, h = _open_without_share_delete(target)
    threading.Timer(0.06, lambda: k32.CloseHandle(ctypes.c_void_p(h))).start()
    afs.edit_exact(target, "one", "two", root=project)
    assert target.read_text(encoding="utf-8") == "two"


@windows_only
def test_hidden_attribute_kept_after_edit(env):
    _, project, _, _, afs = env
    target = project / "hidden.txt"
    target.write_text("one", encoding="utf-8")
    ctypes.windll.kernel32.SetFileAttributesW(str(target), 0x2)
    afs.edit_exact(target, "one", "two", root=project)
    assert ctypes.windll.kernel32.GetFileAttributesW(str(target)) & 0x2
    assert target.read_text(encoding="utf-8") == "two"


@windows_only
def test_win_replace_pinned_is_the_path_used(env, monkeypatch):
    _, project, _, _, afs = env
    calls = []
    real = afs_mod._win_replace_pinned
    monkeypatch.setattr(afs_mod, "_win_replace_pinned", lambda *a, **k: (calls.append(a), real(*a, **k))[1])
    monkeypatch.setattr(afs_mod, "safe_replace_with_backoff",
                        lambda *a, **k: pytest.fail("path-based replace must not be used"))
    target = project / "w.txt"
    target.write_text("one", encoding="utf-8")
    res = afs.edit_exact(target, "one", "two", root=project)
    assert afs.rollback_transaction(res.transaction_id)
    assert len(calls) == 2 and target.read_text(encoding="utf-8") == "one"


# ---- recovery after the process is really killed (all platforms) ---------------------------------------------

CHILD = textwrap.dedent('''
    import sys, time
    from pathlib import Path
    import prometheus.actions.atomic_fs as m
    from prometheus.safety.paths import PathPolicy
    home, data, code, project, target, ready, when = sys.argv[1:8]
    afs = m.AtomicFiles(PathPolicy(home=Path(home), data_dir=Path(data), code_dir=Path(code)),
                        backup_dir=Path(data) / "backups")
    real = m.replace_pinned
    def hang_before(src, dst, root, *a, **k):
        Path(ready).write_text("x"); time.sleep(60)
    def hang_after(src, dst, root, *a, **k):
        real(src, dst, root, *a, **k); Path(ready).write_text("x"); time.sleep(60)
    m.replace_pinned = hang_before if when == "before" else hang_after
    afs.edit_exact(Path(target), "one", "two", root=Path(project))
''')


@pytest.mark.parametrize("when,expect,content", [("before", "rolled_back", "one"), ("after", "committed", "two")])
def test_recovery_after_process_killed_mid_edit(env, tmp_path, when, expect, content):
    home, project, data, policy, _ = env
    target = project / "k.txt"
    target.write_text("one", encoding="utf-8")
    ready = tmp_path / "ready"
    script = tmp_path / "child.py"
    script.write_text(CHILD, encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(script), str(home), str(data), str(home / "code" / "prometheus"),
                             str(project), str(target), str(ready), when],
                            cwd=str(Path(__file__).resolve().parents[1]))
    try:
        end = time.monotonic() + 30
        while not ready.exists() and time.monotonic() < end and proc.poll() is None:
            time.sleep(0.05)
        assert ready.exists(), "child never reached the replace step"
    finally:
        proc.kill()                                   # a real kill (TerminateProcess on Windows): no cleanup runs
        proc.wait(10)
    afs = AtomicFiles(policy=policy, backup_dir=data / "backups")
    out = afs.recover_interrupted_transactions()
    assert any(r.endswith(f": {expect}") for r in out), out
    assert target.read_text(encoding="utf-8") == content
    assert not [p for p in project.iterdir() if ".tmp_" in p.name]
    assert afs.recover_interrupted_transactions() == []      # settled
