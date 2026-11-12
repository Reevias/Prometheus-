"""Tests for the five open M1 audit findings and the bugs found while fixing them (12 Nov 2026)."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import random
import sys

import pytest

import prometheus.actions.atomic_fs as afs_mod
import prometheus.actions.terminal as term
from prometheus.actions.atomic_fs import AtomicFiles, PathSecurityError, ConcurrencyError, replace_pinned
from prometheus.safety.paths import PathPolicy


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


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


# ---- Finding 1: caller env overrides can't smuggle secrets ------------------------------------------------

@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "github_token", "MY_SECRET", "DB_PASSWORD", "AUTH_HEADER"])
def test_override_with_secret_name_refused(name):
    with pytest.raises(ValueError):
        term.build_isolated_environment({name: "x"})


def test_override_with_secret_value_refused():
    with pytest.raises(ValueError):
        term.build_isolated_environment({"HARMLESS": "sk-ant-api03-" + "a" * 40})


def test_override_with_saved_secret_value_refused(monkeypatch):
    saved = "my-very-own-saved-key-1234567890"
    monkeypatch.setattr(term, "get_secret", lambda n: saved if n == next(iter(term.KEY_NAMES)) else None)
    with pytest.raises(ValueError):
        term.build_isolated_environment({"CONFIG": f"prefix {saved}"})


def test_harmless_override_passes():
    env = term.build_isolated_environment({"PYTHONIOENCODING": "utf-8", "MODE": "test"})
    assert env["PYTHONIOENCODING"] == "utf-8" and env["MODE"] == "test"


@pytest.mark.parametrize("name", ["", "A=B", "NUL\0"])
def test_override_bad_names_refused(name):
    with pytest.raises(ValueError):
        term.build_isolated_environment({name: "x"})


# ---- Kill bug: stopping a command must not kill Prometheus's own process group ---------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_timeout_kills_command_not_us(env):
    _, project, _, policy, _ = env
    t = term.AsyncTerminal(policy)
    res = asyncio.run(t.execute([sys.executable, "-c", "import time; time.sleep(30)"], project, timeout=0.5))
    assert res.timed_out
    assert os.getpgrp() > 0   # still alive: before the fix the whole test run was killed here


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_kill_tree_never_signals_own_group(monkeypatch):
    calls = []
    monkeypatch.setattr(term.os, "killpg", lambda g, s: calls.append(("killpg", g)))
    monkeypatch.setattr(term.os, "kill", lambda p, s: calls.append(("kill", p)))
    me = os.getpid()
    term.kill_process_tree(me)          # same group as us: must fall back to a single-process kill
    assert ("killpg", os.getpgrp()) not in calls
    assert calls == [("kill", me)]


# ---- Finding 2: resume failure is fail-safe; spawn failure closes the job --------------------------------

class FakeJob:
    instances: list = []

    def __init__(self):
        self.closed = False
        FakeJob.instances.append(self)

    def assign_process(self, pid):
        return True

    def close(self):
        self.closed = True


def _fake_windows(monkeypatch):
    real_exec = asyncio.create_subprocess_exec

    async def exec_no_flags(*argv, creationflags=0, **kw):
        return await real_exec(*argv, start_new_session=True, **kw)

    FakeJob.instances = []
    monkeypatch.setattr(term.sys, "platform", "win32")
    monkeypatch.setattr(term, "WinJobManager", FakeJob)
    monkeypatch.setattr(term.asyncio, "create_subprocess_exec", exec_no_flags)
    monkeypatch.setattr(term.subprocess, "CREATE_NO_WINDOW", 0, raising=False)


@pytest.mark.skipif(sys.platform == "win32", reason="simulates Windows on POSIX")
def test_resume_failure_kills_process_and_closes_job(env, monkeypatch):
    _, project, _, policy, _ = env
    _fake_windows(monkeypatch)
    started = []

    def boom(pid):
        started.append(pid)
        raise OSError("ResumeThread failed")

    monkeypatch.setattr(term, "resume_process_threads", boom)
    monkeypatch.setattr(term, "kill_process_tree", lambda pid: os.kill(pid, 9))
    t = term.AsyncTerminal(policy)
    with pytest.raises(RuntimeError, match="could not resume"):
        asyncio.run(t.execute([sys.executable, "-c", "import time; time.sleep(30)"], project, timeout=5))
    assert FakeJob.instances[-1].closed
    with pytest.raises(ProcessLookupError):
        os.kill(started[0], 0)   # the process is gone


def test_spawn_failure_closes_job(env, monkeypatch):
    _, project, _, policy, _ = env

    async def fail(*a, **k):
        raise FileNotFoundError("no such program")

    FakeJob.instances = []
    monkeypatch.setattr(term, "WinJobManager", FakeJob)
    monkeypatch.setattr(term.asyncio, "create_subprocess_exec", fail)
    with pytest.raises(FileNotFoundError):
        asyncio.run(term.AsyncTerminal(policy).execute(["nope"], project))
    assert FakeJob.instances[-1].closed


def test_resume_threads_is_noop_off_windows():
    if sys.platform != "win32":
        assert term.resume_process_threads(os.getpid()) == 0


# ---- Finding 3: read_window streams instead of loading the whole file ------------------------------------

def _expected(text, start, count):
    lines = text.splitlines(keepends=True)
    return "".join(lines[start - 1:start - 1 + count]), len(lines)


@pytest.mark.parametrize("seed", range(6))
def test_read_window_matches_splitlines_with_tiny_pieces(env, monkeypatch, seed):
    _, project, _, _, afs = env
    rnd = random.Random(seed)
    alphabet = ["a", "b", "é", "\n", "\r", "\r\n", "\x0c", " ", " "]
    text = "".join(rnd.choice(alphabet) for _ in range(400))
    f = project / "mixed.txt"
    f.write_bytes(text.encode("utf-8"))
    monkeypatch.setattr(afs_mod, "LINE_PIECE_CHARS", rnd.choice([1, 2, 3, 7]))
    for start, count in [(1, 5), (3, 10), (1, 1000), (50, 7)]:
        res = afs.read_window(f, start_line=start, line_count=count)
        want, total = _expected(text, start, count)
        assert res.total_lines == total
        assert res.content == want, (start, count)


def test_read_window_memory_stays_small_on_big_file(env):
    import tracemalloc
    _, project, _, _, afs = env
    f = project / "big.txt"
    with open(f, "w", encoding="utf-8") as out:
        for i in range(400_000):
            out.write(f"line {i} with some padding text to make it longer\n")   # about 21 MB
    tracemalloc.start()
    try:
        res = afs.read_window(f, start_line=399_999, line_count=5)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert res.total_lines == 400_000 and res.content.startswith("line 399998 ")
    assert peak < 4 * 1024 * 1024, peak     # the old reader held the whole file (and its lines) in memory


def test_read_window_latin1_and_bom(env):
    _, project, _, _, afs = env
    f = project / "latin.txt"
    f.write_bytes("caf\xe9\r\nna\xefve\r\n".encode("latin-1"))
    res = afs.read_window(f)
    assert res.encoding == "latin-1" and res.newline == "\r\n" and res.content == "café\r\nnaïve\r\n"
    g = project / "bom.txt"
    g.write_bytes(b"\xef\xbb\xbfhi\nthere\n")
    res = afs.read_window(g)
    assert res.encoding == "utf-8-sig" and res.content == "hi\nthere\n"


# ---- Finding 4: journal paths and backups are validated -----------------------------------------------

def _journal(afs, **rec):
    with open(afs.transactions_log, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec) + "\n")


def test_recovery_refuses_target_outside_allowed_folders(env, tmp_path):
    home, project, data, _, afs = env
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me", encoding="utf-8")
    backup = afs.backup_dir / "tx_evil_outside.txt.bak"
    backup.write_text("evil", encoding="utf-8")
    _journal(afs, tx_id="tx_evil", state="staged", target_path=str(outside), backup_path=str(backup),
             old_sha256=sha(b"evil"), new_sha256=sha(b"keep me"))
    out = afs.recover_interrupted_transactions()
    assert any(r.startswith("tx_evil: skipped_unsafe") for r in out)
    assert outside.read_text(encoding="utf-8") == "keep me"


def test_recovery_refuses_backup_outside_backup_folder(env):
    _, project, _, _, afs = env
    target = project / "t.txt"
    target.write_text("new", encoding="utf-8")
    fake = project / "tx_b1_t.txt.bak"
    fake.write_text("planted", encoding="utf-8")
    _journal(afs, tx_id="tx_b1", state="staged", target_path=str(target), backup_path=str(fake),
             old_sha256=sha(b"planted"), new_sha256=sha(b"new"))
    out = afs.recover_interrupted_transactions()
    assert any(r.startswith("tx_b1: skipped_unsafe") for r in out)
    assert target.read_text(encoding="utf-8") == "new"


def test_recovery_refuses_tmp_path_elsewhere(env, tmp_path):
    _, project, _, _, afs = env
    target = project / "t.txt"
    target.write_text("x", encoding="utf-8")
    victim = tmp_path / "victim.txt"
    victim.write_text("don't delete", encoding="utf-8")
    _journal(afs, tx_id="tx_t1", state="staged", target_path=str(target), tmp_path=str(victim),
             old_sha256=sha(b"x"), new_sha256="n")
    out = afs.recover_interrupted_transactions()
    assert any(r.startswith("tx_t1: skipped_unsafe") for r in out)
    assert victim.exists()


def test_recovery_does_not_restore_tampered_backup(env):
    _, project, _, _, afs = env
    target = project / "t.txt"
    target.write_text("new", encoding="utf-8")
    backup = afs.backup_dir / "tx_d1_t.txt.bak"
    backup.write_text("tampered", encoding="utf-8")
    _journal(afs, tx_id="tx_d1", state="staged", target_path=str(target), backup_path=str(backup),
             old_sha256=sha(b"original"), new_sha256=sha(b"new"))
    out = afs.recover_interrupted_transactions()
    assert any(r.startswith("tx_d1: failed") and "hash mismatch" in r for r in out)
    assert target.read_text(encoding="utf-8") == "new"


def test_recovery_leaves_file_changed_since(env):
    _, project, _, _, afs = env
    target = project / "t.txt"
    target.write_text("owner edited this later", encoding="utf-8")
    backup = afs.backup_dir / "tx_c1_t.txt.bak"
    backup.write_text("original", encoding="utf-8")
    _journal(afs, tx_id="tx_c1", state="staged", target_path=str(target), backup_path=str(backup),
             old_sha256=sha(b"original"), new_sha256=sha(b"new"))
    out = afs.recover_interrupted_transactions()
    assert any(r.startswith("tx_c1: skipped_changed_since") for r in out)
    assert target.read_text(encoding="utf-8") == "owner edited this later"


def test_recovery_undoes_unconfirmed_write_and_keeps_backup(env):
    _, project, _, _, afs = env
    target = project / "t.txt"
    target.write_text("new", encoding="utf-8")
    backup = afs.backup_dir / "tx_u1_t.txt.bak"
    backup.write_text("original", encoding="utf-8")
    _journal(afs, tx_id="tx_u1", state="staged", target_path=str(target), backup_path=str(backup),
             old_sha256=sha(b"original"), new_sha256=sha(b"new"))
    out = afs.recover_interrupted_transactions()
    assert "tx_u1: restored_from_backup" in out
    assert target.read_text(encoding="utf-8") == "original" and backup.exists()
    assert afs.recover_interrupted_transactions() == []   # marked done


def test_rollback_refuses_when_file_changed_since(env):
    _, project, _, _, afs = env
    target = project / "r.txt"
    target.write_text("one", encoding="utf-8")
    res = afs.edit_exact(target, "one", "two", root=project)
    target.write_text("three (owner's later edit)", encoding="utf-8")
    with pytest.raises(ConcurrencyError):
        afs.rollback_transaction(res.transaction_id)
    assert target.read_text(encoding="utf-8") == "three (owner's later edit)"


def test_rollback_keeps_backup(env):
    _, project, _, _, afs = env
    target = project / "r.txt"
    target.write_text("one", encoding="utf-8")
    res = afs.edit_exact(target, "one", "two", root=project)
    assert afs.rollback_transaction(res.transaction_id)
    assert target.read_text(encoding="utf-8") == "one" and res.backup_path.exists()


# ---- Finding 5: replacement with the folder pinned -----------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="folder-relative rename is POSIX only (documented)")
def test_replace_pinned_refuses_swapped_parent(env, tmp_path):
    _, project, _, _, _ = env
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    sub = project / "sub"
    sub.mkdir()
    src = sub / ".f.tmp"
    src.write_text("new", encoding="utf-8")
    # an attacker swaps the folder for a link to somewhere outside the project
    os.rename(sub, project / "sub_moved")
    os.symlink(elsewhere, sub)
    with pytest.raises(PathSecurityError):
        replace_pinned(sub / ".f.tmp", sub / "f.txt", project)
    assert not (elsewhere / "f.txt").exists()


def test_replace_pinned_requires_same_folder(env):
    _, project, _, _, _ = env
    (project / "a").mkdir()
    src = project / "a" / "x"
    src.write_text("1", encoding="utf-8")
    with pytest.raises(PathSecurityError):
        replace_pinned(src, project / "y", project)


def test_replace_pinned_replaces(env):
    _, project, _, _, _ = env
    src, dst = project / ".n.tmp", project / "n.txt"
    dst.write_text("old", encoding="utf-8")
    src.write_text("new", encoding="utf-8")
    replace_pinned(src, dst, project)
    assert dst.read_text(encoding="utf-8") == "new" and not src.exists()
