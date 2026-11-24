"""M5 step 5.3: child agents at once (RAM-aware limit, Job Objects / process groups, clean-up, status)."""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from prometheus.actions.terminal import AsyncTerminal, WinJobManager
from prometheus.jobs import graph as g
from prometheus.jobs import limits
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, RUNNING
from prometheus.jobs.store import JobStore
from prometheus.safety.killswitch import KillSwitch
from prometheus.safety.paths import PathPolicy

GB = 1024 ** 3


def _until(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def gone(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return True


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs.db")


# -- item 1 and 2: 2 at once, 3 with more than 3 GB free, nothing under 1 GB -----------------------------------
@pytest.mark.parametrize("free_gb, size", [(2.0, 2), (3.0, 2), (3.5, 3), (7.0, 3)])
def test_two_at_once_or_three_with_more_than_3_gb_free(free_gb, size):
    slots = limits.RamSlots(free_bytes=lambda: free_gb * GB)
    got = [slots.try_acquire() for _ in range(4)]
    assert got.count(True) == size


def test_nothing_new_starts_under_1_gb_free_and_the_reason_is_shown():
    free = [0.8 * GB]
    slots = limits.RamSlots(free_bytes=lambda: free[0])
    assert slots.try_acquire() is False
    assert "memory" in slots.reason.lower() and "0.8 GB" in slots.reason
    free[0] = 2 * GB                                    # memory came back: checked again before each start
    assert slots.try_acquire() is True


def test_the_reading_is_the_real_psutil_one(monkeypatch):
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=int(0.5 * GB)))
    slots = limits.RamSlots()
    assert slots.try_acquire() is False and "0.5 GB" in slots.reason
    monkeypatch.setattr(psutil, "virtual_memory", lambda: SimpleNamespace(available=int(5 * GB)))
    assert [slots.try_acquire() for _ in range(4)].count(True) == 3


def test_an_unreadable_memory_reading_is_never_guessed(monkeypatch):
    def broken():
        raise OSError("no reading")
    monkeypatch.setattr(psutil, "virtual_memory", broken)
    slots = limits.RamSlots()
    assert [slots.try_acquire() for _ in range(3)].count(True) == 2     # the safe default, never "3"
    assert slots.free_gb is None


def test_graphs_use_the_ram_aware_limit_by_default(store):
    assert isinstance(g.GraphRunner(store).slots, limits.RamSlots)


def test_five_steps_only_two_run_and_the_rest_wait_for_a_slot(store):
    slots = limits.RamSlots(free_bytes=lambda: 2 * GB)
    graphs = g.GraphRunner(store, JobRunner(store), slots=slots)
    release = []

    def hold(ctx, inputs):
        release.append(ctx.step)
        ctx.wait(10)

    gid = graphs.create("five", [{"id": f"s{i}", "func": hold} for i in range(5)])
    graphs.start(gid)
    assert _until(lambda: len(release) == 2)
    time.sleep(0.3)
    steps = store.list_jobs(parent_id=gid)
    assert [j.state for j in steps].count(RUNNING) == 2 and len(release) == 2
    assert all(j.progress == "Waiting for a free slot" for j in steps if j.state != RUNNING)
    graphs.cancel(gid)


def test_low_memory_stops_a_third_step_and_says_why(store):
    reads = []

    def free():                                         # plenty for the first two starts, then memory runs low
        reads.append(1)
        return 5 * GB if len(reads) <= 2 else 0.5 * GB

    graphs = g.GraphRunner(store, JobRunner(store), slots=limits.RamSlots(free_bytes=free))
    started = []

    def hold(ctx, inputs):
        started.append(ctx.step)
        ctx.wait(10)

    gid = graphs.create("three", [{"id": "a", "func": hold}, {"id": "b", "func": hold}, {"id": "c", "func": hold}])
    graphs.start(gid)
    assert _until(lambda: "memory" in store.get_job(gid).progress.lower())
    time.sleep(0.3)
    assert len(started) == 2 and "0.5 GB free" in store.get_job(gid).progress
    graphs.cancel(gid)


# -- item 3: programs a child starts are capped and grouped ---------------------------------------------------
def test_the_job_object_takes_a_memory_cap():
    job = WinJobManager(memory_limit=256 * 1024 * 1024)
    try:
        assert job.memory_limit == 256 * 1024 * 1024
        if sys.platform == "win32":
            assert job.job_handle and job.limits_set()          # kill-on-close + job memory, read back
    finally:
        job.close()
        job.close()                                              # closing twice is harmless


def test_the_memory_cap_is_the_child_size():
    assert limits.CHILD_MEMORY_CAP == 1536 * 1024 * 1024


@pytest.fixture
def terminal(tmp_path):
    home = tmp_path / "home"
    data, code, project = home / "data", home / "code", home / "project"
    for d in (data, code, project):
        d.mkdir(parents=True)
    return AsyncTerminal(policy=PathPolicy(home=home, data_dir=data, code_dir=code)), project


TREE = ("import subprocess, sys, time, pathlib\n"
        "c = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "pathlib.Path(sys.argv[1]).write_text(f'{c.pid} ' + str(__import__('os').getpid()))\n"
        "time.sleep(60)\n")


def _tree_step(terminal, project, pidfile):
    def run(ctx, inputs):
        asyncio.run(terminal.execute([sys.executable, "-c", TREE, str(pidfile)], cwd=project, timeout=60))
    return run


def _pids(pidfile) -> list[int]:
    return [int(x) for x in pidfile.read_text().split()]


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object memory caps are Windows only")
def test_on_windows_a_child_program_over_the_cap_is_stopped(store, terminal, monkeypatch):
    term, project = terminal
    monkeypatch.setattr(limits, "CHILD_MEMORY_CAP", 200 * 1024 * 1024)
    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2)
    out = {}

    def big(ctx, inputs):
        out["res"] = asyncio.run(term.execute([sys.executable, "-c", "b = bytearray(600 * 1024 * 1024)"],
                                              cwd=project, timeout=60))

    gid = graphs.create("big", [{"id": "a", "func": big}])
    graphs.start(gid)
    assert graphs.wait(gid, 60) == DONE
    assert out["res"].exit_code != 0 and "MemoryError" in out["res"].stderr


def test_a_program_outside_a_child_job_has_no_cap(terminal):
    term, project = terminal
    res = asyncio.run(term.execute([sys.executable, "-c", "b = bytearray(300 * 1024 * 1024); print('ok')"],
                                   cwd=project, timeout=60))
    assert res.exit_code == 0 and "ok" in res.stdout


# -- item 4 and 6: clean-up ------------------------------------------------------------------------------------
def test_cancelling_a_step_ends_its_programs(store, terminal, tmp_path):
    term, project = terminal
    pidfile = tmp_path / "pids"
    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2)
    gid = graphs.create("tree", [{"id": "a", "func": _tree_step(term, project, pidfile)}])
    graphs.start(gid)
    assert _until(lambda: pidfile.exists() and len(pidfile.read_text().split()) == 2)
    graphs.cancel(gid)
    assert _until(lambda: all(gone(p) for p in _pids(pidfile))), _pids(pidfile)
    assert _until(lambda: graphs.idle(gid))


def test_stop_all_leaves_no_child_processes(store, terminal, tmp_path):
    term, project = terminal
    ks = KillSwitch()
    limits.connect_killswitch(ks)
    pidfile = tmp_path / "pids"
    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2, killswitch=ks)
    gid = graphs.create("tree", [{"id": "a", "func": _tree_step(term, project, pidfile)}])
    graphs.start(gid)
    assert _until(lambda: pidfile.exists() and len(pidfile.read_text().split()) == 2)
    ks.trigger("test")
    assert _until(lambda: all(gone(p) for p in _pids(pidfile))), _pids(pidfile)
    assert _until(lambda: graphs.idle(gid))
    assert limits.running_programs() == {}


def test_leftovers_from_a_crash_are_ended_at_start_up_but_never_a_reused_pid(store):
    kw = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
    left = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kw)
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **kw)
    try:
        created = psutil.Process(left.pid).create_time()
        job = store.create_job("graph_step", "crashed step", payload={
            "programs": [{"pid": left.pid, "created": created},
                         {"pid": other.pid, "created": created - 1000}]})       # same pid number, another program
        assert limits.end_leftovers(store) == 1
        assert _until(lambda: left.poll() is not None)
        assert other.poll() is None
        assert store.get_job(job.id).payload["programs"] == []
    finally:
        for p in (left, other):
            p.kill()
            p.wait()


# -- item 5: status of each child ------------------------------------------------------------------------------
def test_status_shows_state_run_time_and_real_memory(store, terminal, tmp_path):
    term, project = terminal
    pidfile = tmp_path / "pids"
    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2)
    gid = graphs.create("tree", [{"id": "a", "func": _tree_step(term, project, pidfile)},
                                 {"id": "b", "func": lambda ctx, inputs: ctx.wait(10)}])
    graphs.start(gid)
    assert _until(lambda: pidfile.exists() and len(pidfile.read_text().split()) == 2)
    steps = {j.payload["step"]: j for j in store.list_jobs(parent_id=gid)}
    a, b = limits.child_status(store, steps["a"].id), limits.child_status(store, steps["b"].id)
    assert a["state"] == RUNNING and a["seconds"] > 0 and a["memory_bytes"] > 1_000_000
    assert b["memory_bytes"] is None                     # no program of its own: nothing to measure, nothing made up
    graphs.cancel(gid)
    assert _until(lambda: all(gone(p) for p in _pids(pidfile)))
    assert limits.child_status(store, steps["a"].id)["state"] == CANCELLED
