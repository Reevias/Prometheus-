"""M0/M1 remediation (12 Nov 2026): retry worker, secret detection, restore through handles, fail-closed replace."""
from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import prometheus.actions.atomic_fs as afs_mod
from prometheus import resume
from prometheus.actions.atomic_fs import AtomicFiles, PathSecurityError, TransactionError
from prometheus.safety.paths import PathPolicy
from prometheus.safety.secrets import find_secrets


# ---- retry worker (prometheus/resume.py) ------------------------------------------------------------------

class KVStore:
    def __init__(self, status="done"):
        self.kv, self.status, self.lock = {}, status, threading.Lock()

    def kv_get(self, key, default=None):
        with self.lock:
            return json.loads(json.dumps(self.kv.get(key, default)))

    def kv_set(self, key, value):
        with self.lock:
            self.kv[key] = json.loads(json.dumps(value))

    def latest_task(self, chat_id):
        return {"status": self.status}


def make_services(start_task, status="done", notify=None):
    notes = []
    return SimpleNamespace(
        store=KVStore(status), running={}, settings=SimpleNamespace(paused=False, features_off=[]),
        start_task=start_task, notify=notify or (lambda kind, text: notes.append(text)), notes=notes)


@pytest.fixture(autouse=True)
def features_on(monkeypatch):
    import prometheus.features as features
    monkeypatch.setattr(features, "on", lambda settings, key: True)
    monkeypatch.setattr(features, "paused", lambda settings: False)
    resume._IN_FLIGHT.clear()
    yield
    resume._IN_FLIGHT.clear()


def wait_for(cond, secs=5.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def queued(s):
    return s.store.kv_get(resume.KV, [])


def test_start_task_raising_is_recorded_and_retried_later():
    def boom(chat_id, text):
        raise RuntimeError("database is locked")

    s = make_services(boom)
    resume.note_limit(s.store, 1, "q", "rate limited", now=0)
    r = resume.Resumer(s, clock=lambda: 10 ** 6)
    assert r.tick() == 1
    assert wait_for(lambda: queued(s) and queued(s)[0].get("last_error"))
    item = queued(s)[0]
    assert item["tries"] == 2 and item["due"] == 10 ** 6 + resume.WAITS[1] * 60     # counted, normal back-off
    assert "RuntimeError" in item["last_error"] and "database" not in item["last_error"]   # no raw message
    assert wait_for(lambda: not resume._IN_FLIGHT)                                       # the worker finished
    assert r.tick() == 0                                                                 # not due yet


def test_unrecoverable_request_is_purged_and_owner_told():
    def boom(chat_id, text):
        raise RuntimeError("broken")

    s = make_services(boom)
    s.store.kv_set(resume.KV, [{"id": "a", "chat_id": 1, "text": "q", "tries": len(resume.WAITS), "due": 0}])
    assert resume.Resumer(s, clock=lambda: 10 ** 6).tick() == 1
    assert wait_for(lambda: queued(s) == [])
    assert wait_for(lambda: any("stopped trying" in n for n in s.notes))


def test_notification_failure_does_not_stop_cleanup():
    def notify(kind, text):
        raise OSError("toast service down")

    started = []
    s = make_services(lambda c, t: started.append(c) or None, notify=notify)
    resume.note_limit(s.store, 1, "q", "rate limited", now=0)
    assert resume.Resumer(s, clock=lambda: 10 ** 6).tick() == 1
    assert wait_for(lambda: queued(s) == [])            # answered and removed even though the notification failed
    assert wait_for(lambda: not resume._IN_FLIGHT)


def test_malformed_entries_are_dropped():
    s = make_services(lambda c, t: None)
    s.store.kv_set(resume.KV, [{"chat_id": "x"}, "junk", {"chat_id": 1, "text": "ok", "tries": 0, "due": 10 ** 9}])
    q = resume._queue(s.store)
    assert len(q) == 1 and q[0]["text"] == "ok" and q[0]["id"]


def test_many_queued_requests_all_run_once():
    calls, lock = [], threading.Lock()

    def start(chat_id, text):
        with lock:
            calls.append((chat_id, text))

    s = make_services(start)
    for chat in range(1, 6):
        resume.note_limit(s.store, chat, f"q{chat}", "rate limited", now=0)
    r = resume.Resumer(s, clock=lambda: 10 ** 6)
    assert r.tick() == 5
    assert wait_for(lambda: queued(s) == [])
    assert sorted(calls) == [(c, f"q{c}") for c in range(1, 6)]


def test_concurrent_ticks_never_start_a_request_twice():
    gate, calls = threading.Event(), []

    def slow_start(chat_id, text):
        calls.append(chat_id)
        gate.wait(2)

    s = make_services(slow_start)
    for chat in range(1, 4):
        resume.note_limit(s.store, chat, f"q{chat}", "rate limited", now=0)
    r = resume.Resumer(s, clock=lambda: 10 ** 6)
    counts = []
    threads = [threading.Thread(target=lambda: counts.append(r.tick())) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sum(counts) == 3                          # each request started by exactly one tick
    # even if the due time is pushed back to now, an in-flight request is not started again
    q = queued(s)
    for item in q:
        item["due"] = 0
    s.store.kv_set(resume.KV, q)
    assert r.tick() == 0
    gate.set()
    assert wait_for(lambda: queued(s) == [])
    assert sorted(calls) == [1, 2, 3]


def test_concurrent_queue_updates_lose_nothing():
    s = make_services(lambda c, t: None)

    def add(chat):
        for i in range(5):
            resume.note_limit(s.store, chat, f"q{chat}-{i}", "rate limited", now=0)

    threads = [threading.Thread(target=add, args=(c,)) for c in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(queued(s)) == min(20, resume.MAX_WAITING)


def test_a_worker_removes_only_its_own_entry():
    s = make_services(lambda c, t: None)
    s.store.kv_set(resume.KV, [{"id": "mine", "chat_id": 1, "text": "q", "tries": 1, "due": 0},
                               {"id": "theirs", "chat_id": 1, "text": "q", "tries": 1, "due": 10 ** 12}])
    resume._without(s.store, "mine")
    assert [q["id"] for q in queued(s)] == ["theirs"]


def test_successful_resume_still_works_and_paused_task_stays():
    s = make_services(lambda c, t: None, status="paused")      # hit a limit again: keep waiting
    resume.note_limit(s.store, 1, "q", "rate limited", now=0)
    assert resume.Resumer(s, clock=lambda: 10 ** 6).tick() == 1
    assert wait_for(lambda: not resume._IN_FLIGHT)
    assert len(queued(s)) == 1


# ---- secret detection (prometheus/safety/secrets.py) ---------------------------------------------------------

@pytest.mark.parametrize("prefix", ["ghp_", "gho_", "ghu_", "ghs_", "ghr_"])
def test_classic_github_token_boundary(prefix):
    assert find_secrets(prefix + "A" * 19, saved=[]) == []
    assert find_secrets(prefix + "A" * 20, saved=[]) == ["GitHub token"]
    assert find_secrets(prefix + "aB3" * 12, saved=[]) == ["GitHub token"]
    assert find_secrets(f"token={prefix}{'x' * 20}\n", saved=[]) == ["GitHub token"]


@pytest.mark.parametrize("text", ["ghx_" + "A" * 30, "gh_" + "A" * 30, "xghp_" + "A" * 30, "ghp-" + "A" * 30,
                                  "ghp_" + "A" * 10 + "-" + "A" * 10])
def test_not_github_tokens(text):
    assert "GitHub token" not in find_secrets(text, saved=[])


def test_fine_grained_and_other_secrets_still_found():
    cases = {
        "github_pat_" + "a1_" * 14: "GitHub token",
        "sk-ant-api03-" + "x" * 30: "Anthropic key",
        "sk-or-v1-" + "x" * 30: "OpenRouter key",
        "sk-proj-" + "x" * 40: "OpenAI-style key",
        "AIza" + "x" * 35: "Google API key",
        "AKIA" + "A" * 16: "AWS access key",
        "xoxb-" + "1" * 12: "Slack token",
        "hf_" + "x" * 34: "Hugging Face token",
        "-----BEGIN OPENSSH PRIVATE KEY": "private key",
    }
    for text, label in cases.items():
        assert label in find_secrets(text, saved=[]), label
    assert find_secrets("my key is abcdefghijkl123", saved=["abcdefghijkl123"]) == ["one of your saved keys"]


def test_secret_value_is_never_echoed():
    from prometheus.safety.secrets import secret_problem
    token = "ghp_" + "Z" * 24
    msg = secret_problem(f"push with {token}", saved=[])
    assert msg and token not in msg and "Z" * 10 not in msg


# ---- restore through handles; replace fails closed (prometheus/actions/atomic_fs.py) -------------------------

@pytest.fixture
def env(tmp_path):
    home = tmp_path / "home"
    data = home / "AppData" / "Local" / "Prometheus"
    code = home / "code" / "prometheus"
    project = home / "projects" / "app"
    for d in (data, code, project):
        d.mkdir(parents=True, exist_ok=True)
    policy = PathPolicy(home=home, data_dir=data, code_dir=code)
    return home, project, data, AtomicFiles(policy=policy, backup_dir=data / "backups")


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_restore_uses_what_was_hashed_even_if_backup_swapped_after_check(env, monkeypatch):
    _, project, _, afs = env
    target = project / "t.txt"
    target.write_text("one", encoding="utf-8")
    res = afs.edit_exact(target, "one", "two", root=project)
    backup = res.backup_path
    real_open = os.open

    def swap_then_open(path, flags, *a):          # attacker replaces the backup between any check and the copy
        if str(path) == str(backup) and not getattr(swap_then_open, "done", False):
            swap_then_open.done = True
            backup.write_text("evil", encoding="utf-8")
        return real_open(path, flags, *a)

    monkeypatch.setattr(afs_mod.os, "open", swap_then_open)
    with pytest.raises(TransactionError, match="hash mismatch"):
        afs.rollback_transaction(res.transaction_id)
    assert target.read_text(encoding="utf-8") == "two"          # nothing restored from the swapped backup
    assert not list(project.glob(".t.txt.restore_*"))           # no leftover copy


@pytest.mark.skipif(sys.platform == "win32", reason="symlink creation needs privileges on Windows")
def test_planted_or_leftover_restore_file_never_blocks_or_is_touched(env, tmp_path):
    # Review fix: the restore copy gets a fresh random name, so a file left by a crash (or planted) at an old name
    # can't block a restore forever. It is still never written through and never deleted by us.
    _, project, _, afs = env
    target = project / "t.txt"
    target.write_text("one", encoding="utf-8")
    res = afs.edit_exact(target, "one", "two", root=project)
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    planted = project / f".t.txt.restore_{res.transaction_id}"
    os.symlink(victim, planted)
    leftover = project / f".t.txt.restore_{res.transaction_id}_deadbeef"
    leftover.write_text("crash leftover", encoding="utf-8")
    assert afs.rollback_transaction(res.transaction_id)
    assert target.read_text(encoding="utf-8") == "one"
    assert victim.read_text(encoding="utf-8") == "keep" and planted.is_symlink() and leftover.exists()


def _link_dir(link, target):
    if sys.platform == "win32":
        import subprocess
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        os.symlink(target, link)


def test_backup_reached_through_linked_folder_is_refused_by_handle_check(env, tmp_path):
    # Reaches the handle-containment check itself (O_NOFOLLOW only covers the last name): a folder inside the
    # backup folder that is a link/junction to somewhere else. Runs on Windows (junction) and Linux (symlink).
    _, project, _, afs = env
    target = project / "t.txt"
    target.write_text("two", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "evil.bak").write_bytes(b"one")
    link = afs.backup_dir / "linked"
    _link_dir(link, outside)
    with pytest.raises(TransactionError, match="backup"):
        afs._restore_backup("tx_x", link / "evil.bak", target, sha(b"one"))
    assert target.read_text(encoding="utf-8") == "two"


def test_backup_in_a_subfolder_of_backups_is_refused(env):
    _, project, _, afs = env
    target = project / "t.txt"
    target.write_text("two", encoding="utf-8")
    sub = afs.backup_dir / "sub"
    sub.mkdir()
    (sub / "x.bak").write_bytes(b"one")
    with pytest.raises(TransactionError, match="directly in the backup folder"):
        afs._restore_backup("tx_y", sub / "x.bak", target, sha(b"one"))


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_restored_missing_file_gets_backup_permissions(env):
    _, project, _, afs = env
    target = project / "gone.txt"
    target.write_text("one", encoding="utf-8")
    os.chmod(target, 0o644)
    res = afs.edit_exact(target, "one", "two", root=project)
    target.unlink()
    out = afs.recover_interrupted_transactions()       # committed edits are skipped; restore directly
    afs._restore_backup(res.transaction_id, res.backup_path, target, res.old_sha256)
    assert target.read_text(encoding="utf-8") == "one" and (os.stat(target).st_mode & 0o777) == 0o644


def test_restore_keeps_backup_until_verified_and_after(env):
    _, project, _, afs = env
    target = project / "t.txt"
    target.write_text("one", encoding="utf-8")
    res = afs.edit_exact(target, "one", "two", root=project)
    assert afs.rollback_transaction(res.transaction_id)
    assert target.read_text(encoding="utf-8") == "one" and res.backup_path.read_text(encoding="utf-8") == "one"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX replace path")
def test_replace_fails_closed_without_pinned_rename(env, monkeypatch):
    _, project, _, _ = env
    src, dst = project / ".x.tmp", project / "x.txt"
    src.write_text("new", encoding="utf-8")
    dst.write_text("old", encoding="utf-8")
    monkeypatch.setattr(afs_mod.os, "supports_dir_fd", set())
    called = []
    monkeypatch.setattr(afs_mod, "safe_replace_with_backoff", lambda *a, **k: called.append(a))
    with pytest.raises(PathSecurityError, match="folder-pinned"):
        afs_mod.replace_pinned(src, dst, project)
    assert not called and dst.read_text(encoding="utf-8") == "old"



# ---- review fixes: the store failing must never lose a request or run it twice ----------------------------------

class FlakyStore(KVStore):
    def __init__(self, status="done"):
        super().__init__(status)
        self.fail_sets, self.fail_latest, self.fail_sets_after = 0, False, None
        self.sets = 0

    def kv_set(self, key, value):
        self.sets += 1
        if self.fail_sets > 0:
            self.fail_sets -= 1
            raise RuntimeError("database is locked")
        if self.fail_sets_after is not None and self.sets > self.fail_sets_after:
            raise RuntimeError("database is locked")
        super().kv_set(key, value)

    def latest_task(self, chat_id):
        if self.fail_latest:
            raise RuntimeError("database is locked")
        return super().latest_task(chat_id)


def test_failed_save_in_tick_does_not_strand_the_request():
    calls = []
    s = make_services(lambda c, t: calls.append(c))
    s.store = FlakyStore()
    resume.note_limit(s.store, 1, "q", "rate limited", now=0)
    r = resume.Resumer(s, clock=lambda: 10 ** 6)
    s.store.fail_sets = 1
    with pytest.raises(RuntimeError):
        r.tick()
    assert not resume._IN_FLIGHT                       # nothing marked running
    assert r.tick() == 1                               # the next tick starts it
    assert wait_for(lambda: calls == [1])


def test_worker_that_cannot_start_releases_every_request(monkeypatch):
    s = make_services(lambda c, t: None)
    s.store = FlakyStore()
    for chat in (1, 2, 3):
        resume.note_limit(s.store, chat, f"q{chat}", "rate limited", now=0)
    r = resume.Resumer(s, clock=lambda: 10 ** 6)

    def no_thread(item):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(r, "_run", no_thread)
    s.store.fail_sets_after = s.store.sets + 1       # the tick's save works, every save after it fails
    assert r.tick() == 0
    assert not resume._IN_FLIGHT                       # none stuck as "running" for ever


def test_request_is_not_run_again_when_tidying_up_fails():
    calls = []
    s = make_services(lambda c, t: calls.append(c))
    s.store = FlakyStore(status="done")
    resume.note_limit(s.store, 1, "q", "rate limited", now=0)
    later = {"t": 10 ** 6}
    r = resume.Resumer(s, clock=lambda: later["t"])
    s.store.fail_sets_after = s.store.sets + 1       # removing the answered request fails
    assert r.tick() == 1
    assert wait_for(lambda: calls == [1] and not resume._IN_FLIGHT, 5)
    s.store.fail_sets_after = None
    later["t"] += 2 * 3600                             # long past its one-hour hold
    assert r.tick() == 0                               # answered: never started again
    assert calls == [1] and queued(s) == []            # and it is tidied away


def test_latest_task_failing_after_start_does_not_requeue():
    calls = []
    s = make_services(lambda c, t: calls.append(c))
    s.store = FlakyStore()
    resume.note_limit(s.store, 1, "q", "rate limited", now=0)
    r = resume.Resumer(s, clock=lambda: 10 ** 6)
    s.store.fail_latest = True
    assert r.tick() == 1
    assert wait_for(lambda: not resume._IN_FLIGHT)
    item = queued(s)[0]
    assert item["tries"] == 1 and "last_error" not in item     # not counted as a failed try, not rescheduled early


def test_give_up_threshold_matches_note_limit():
    def boom(chat_id, text):
        raise RuntimeError("x")

    s = make_services(boom)
    s.store.kv_set(resume.KV, [{"id": "a", "chat_id": 1, "text": "q", "tries": len(resume.WAITS) - 1, "due": 0}])
    assert resume.Resumer(s, clock=lambda: 10 ** 6).tick() == 1
    assert wait_for(lambda: queued(s) == [])


@pytest.mark.parametrize("name", ["COM0", "lpt0.txt", "COM\u00b9", "LPT\u00b3.log", "CONIN$", "conout$", "AUX .txt"])
def test_windows_device_names_refused_on_every_platform(name):
    from prometheus.safety.paths import PathBlocked, _check_raw_path
    with pytest.raises(PathBlocked):
        _check_raw_path(name)
