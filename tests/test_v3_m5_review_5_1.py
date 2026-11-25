"""Opus review of M5 step 5.1 (job tracker): each test here failed before its fix."""
from __future__ import annotations

import ast
import gc
import threading
import time
import weakref
from pathlib import Path

from prometheus.jobs.lint import find_untracked
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, RUNNING
from prometheus.jobs.store import JobStore

ROOT = Path(__file__).resolve().parent.parent


def _wait(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


# R1: restart recovery must run before this session starts any job ------------------------------------------
def test_jobs_started_while_prometheus_starts_are_not_marked_restarted(app_client, monkeypatch):
    from prometheus.memory.archive import ChatArchive

    release = threading.Event()
    monkeypatch.setattr(ChatArchive, "sync_all", lambda self: release.wait(60))     # slow Windows machines: 5 s was too short
    _, services = app_client()
    try:
        jobs = services.jobs.list_jobs(kind="archive_sync")
        assert jobs and jobs[-1].state == RUNNING, [(j.state, j.error) for j in jobs]
        assert jobs[-1].error != "Prometheus restarted"
    finally:
        release.set()


# R2: service loops (reminders, sync, ...) are not jobs the owner pauses or resumes ---------------------------
def test_service_loops_are_not_paused_by_stop_all_or_left_paused_by_a_restart(tmp_path):
    store = JobStore(tmp_path / "j.db")
    runner = JobRunner(store)
    stop = threading.Event()
    loop = runner.start_job("reminders", "Reminders loop", stop.wait, args=(5,), service=True)
    user = runner.start_job("task", "A task", stop.wait, args=(5,))
    assert _wait(lambda: store.get_job(loop.id).state == RUNNING and store.get_job(user.id).state == RUNNING)
    store.pause_all("Stopped by user")
    assert store.get_job(loop.id).state == RUNNING        # still really running: never shown as paused
    assert store.get_job(user.id).state == PAUSED
    store.recover_after_restart()                          # as if Prometheus had crashed here
    j = store.get_job(loop.id)
    assert j.state == CANCELLED and "starts again" in j.error
    stop.set()


def test_every_loop_started_through_the_tracker_is_marked_as_a_service():
    missing = []
    for py in sorted((ROOT / "prometheus").rglob("*.py")):
        for node in ast.walk(ast.parse(py.read_text(encoding="utf-8"))):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "start_job"):
                continue
            func = node.args[2] if len(node.args) > 2 else next((k.value for k in node.keywords if k.arg == "func"), None)
            if (isinstance(func, ast.Attribute) and func.attr == "loop") or (isinstance(func, ast.Name) and func.id == "loop"):
                if not any(k.arg == "service" for k in node.keywords):
                    missing.append(f"{py.relative_to(ROOT).as_posix()}:{node.lineno}")
    assert not missing, missing


# R3: a waiting request (resume.py) is finished in the tracker when it is answered or given up ---------------
def test_a_waiting_request_job_is_finished_once_answered(tmp_path):
    from prometheus import resume
    from prometheus.memory.store import Store

    store = Store(tmp_path / "memory.db")
    resume.note_limit(store, chat_id=7, text="hello", error="rate limit exceeded", now=1000.0)
    js = JobStore(store)
    job = js.list_jobs(kind="resume")[0]
    assert job.state == PAUSED
    resume._without(store, job.payload["id"])
    assert js.get_job(job.id).state == DONE


def test_a_waiting_request_job_fails_when_it_is_given_up(tmp_path):
    from prometheus import resume
    from prometheus.memory.store import Store

    store = Store(tmp_path / "memory.db")
    for i in range(len(resume.WAITS) + 1):
        resume.note_limit(store, chat_id=7, text="hello", error="rate limit exceeded", now=1000.0 + i)
    js = JobStore(store)
    assert [j.state for j in js.list_jobs(kind="resume")] == [FAILED]


# R4: the lint test catches every way of starting work in the background, per call ---------------------------
def _scan(tmp_path, code, allow=None):
    pkg = tmp_path / "prometheus"
    pkg.mkdir(exist_ok=True)
    (pkg / "x.py").write_text(code)
    return find_untracked(tmp_path, allow or {})


def test_lint_catches_timers_pools_loop_tasks_and_thread_subclasses(tmp_path):
    code = ("import threading, asyncio, concurrent.futures as cf\n"
            "from concurrent.futures import ThreadPoolExecutor\n"
            "threading.Timer(1, print).start()\n"
            "cf.ThreadPoolExecutor(2)\n"
            "ThreadPoolExecutor(2)\n"
            "asyncio.get_event_loop().create_task(None)\n"
            "asyncio.ensure_future(None)\n"
            "class W(threading.Thread):\n    pass\n")
    found = _scan(tmp_path, code)
    assert len(found) == 6, found


def test_an_allowed_file_may_not_start_more_than_its_listed_threads(tmp_path):
    code = "import threading\nthreading.Thread(target=print).start()\nthreading.Thread(target=print).start()\n"
    assert len(_scan(tmp_path, code, {"prometheus/x.py": ["one listed reason"]})) == 1
    assert _scan(tmp_path, code, {"prometheus/x.py": ["first", "second"]}) == []


def test_the_repo_has_no_unlisted_background_work():
    assert find_untracked(ROOT) == []


# R5: finished threads are not kept for ever ----------------------------------------------------------------
def test_the_runner_does_not_keep_finished_threads(tmp_path):
    runner = JobRunner(JobStore(tmp_path / "j.db"))
    job = runner.start_job("x", "x", lambda: None)
    t = runner.get_thread(job.id)
    t.join(5)
    ref = weakref.ref(t)
    del t
    for _ in range(100):                                   # many jobs later the first one's thread is gone
        runner.get_thread(runner.start_job("x", "x", lambda: None).id).join(5)
    gc.collect()
    assert ref() is None
