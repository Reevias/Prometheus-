"""M6 step 6.1: one way to run tasks (ExecutionRuntime). LocalRuntime wraps M5's runner, graphs and limits."""
from __future__ import annotations

import ast
import inspect
import threading
import time
from pathlib import Path

import pytest

from prometheus.jobs.states import CANCELLED, DONE, PAUSED, PENDING, RUNNING
from prometheus.jobs.store import JobStore
from prometheus.runtime import (ExecutionRuntime, JobSpec, LocalRuntime, RuntimeRefused, Runtimes, find_direct_starts,
                                setup_local)

ROOT = Path(__file__).resolve().parent.parent


def _until(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


@pytest.fixture
def runtimes(tmp_path):
    return setup_local(JobStore(tmp_path / "jobs.db"))


# -- item 1: the runtime's five calls ------------------------------------------------------------------------------
def test_execution_runtime_has_start_status_cancel_events_result():
    for name in ("start", "status", "cancel", "events", "result"):
        assert name in ExecutionRuntime.__abstractmethods__
    with pytest.raises(TypeError):
        ExecutionRuntime()


# -- item 2: LocalRuntime runs M5 jobs with the same results --------------------------------------------------------
def test_a_job_started_locally_runs_and_reports_status_result_and_events(runtimes):
    local = runtimes.local
    job = local.start(JobSpec("calc", "Add two numbers", func=lambda a, b: a + b, args=(2, 3)))
    assert _until(lambda: local.status(job.id).state == DONE)
    assert local.result(job.id) == 5
    events = local.events(job.id)
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert [e["state"] for e in events] == [PENDING, RUNNING, DONE]
    assert [e["state"] for e in local.events(job.id, after=1)] == [RUNNING, DONE]


def test_cancel_and_restart_are_events_too(runtimes, tmp_path):
    local = runtimes.local
    go = threading.Event()
    running = local.start(JobSpec("wait", "Waits", func=lambda: go.wait(5)))
    other = local.start(JobSpec("wait", "Waits too", func=lambda: go.wait(5)))
    assert _until(lambda: local.status(running.id).state == RUNNING and local.status(other.id).state == RUNNING)
    assert local.cancel(running.id) is True
    assert local.status(running.id).state == CANCELLED
    assert local.events(running.id)[-1]["state"] == CANCELLED
    JobStore(tmp_path / "jobs.db").recover_after_restart()          # as if Prometheus had restarted
    assert local.events(other.id)[-1]["state"] == PAUSED
    assert local.events(other.id)[-1]["text"] == "Prometheus restarted"
    go.set()


def test_same_job_record_as_m5(runtimes):
    job = runtimes.local.start(JobSpec("reminders", "Reminders loop", func=lambda: None, service=True,
                                       payload={"x": 1}))
    rec = runtimes.local.status(job.id)
    assert (rec.kind, rec.title, rec.payload) == ("reminders", "Reminders loop", {"x": 1, "service": True})


def test_graphs_start_and_cancel_through_the_runtime(runtimes):
    local = runtimes.local
    gid = local.start_graph("two", [{"id": "a", "func": lambda ctx, inputs: ctx.wait(10)}, {"id": "b", "needs": ["a"],
                                                                                              "func": lambda c, i: 1}])
    assert _until(lambda: local.store.list_jobs(parent_id=gid)[0].state == RUNNING)
    assert local.cancel(gid) is True
    assert local.status(gid).state == CANCELLED
    assert all(j.state == CANCELLED for j in local.store.list_jobs(parent_id=gid))
    assert local.events(gid)[-1]["state"] == CANCELLED


def test_the_limits_are_the_m5_ones(runtimes):
    from prometheus.jobs import limits

    assert runtimes.local.graphs.slots is limits.child_slots()


# -- item 4: a runtime choice per job, local by default; another runtime only with a yes -----------------------------
class FakeCloud(LocalRuntime):
    name = "cloudroom"

    def __init__(self, store):
        super().__init__(store=store)
        self.started = []

    def start(self, spec):
        self.started.append(spec.title)
        return super().start(spec)


def test_the_default_runtime_is_local(runtimes):
    job = runtimes.start(JobSpec("x", "x", func=lambda: None))
    assert _until(lambda: runtimes.local.status(job.id).state == DONE)
    assert JobSpec("x", "x").runtime == "local"


def test_a_runtime_that_is_not_set_up_is_refused_and_nothing_is_made(runtimes):
    before = len(runtimes.local.store.list_jobs())
    with pytest.raises(RuntimeRefused, match="isn't set up"):
        runtimes.start(JobSpec("x", "x", func=lambda: None, runtime="cloudroom", approval="a1"))
    assert len(runtimes.local.store.list_jobs()) == before


def test_moving_needs_the_owners_yes(runtimes):
    cloud = FakeCloud(runtimes.local.store)
    runtimes.register(cloud)
    with pytest.raises(RuntimeRefused, match="yes"):
        runtimes.start(JobSpec("x", "move me", func=lambda: None, runtime="cloudroom"))
    with pytest.raises(RuntimeRefused, match="yes"):
        runtimes.start(JobSpec("x", "move me", func=lambda: None, runtime="cloudroom", approval="not-approved"))
    assert cloud.started == []
    runtimes.approval_check = lambda approval, spec: approval == "yes-123"
    runtimes.start(JobSpec("x", "move me", func=lambda: None, runtime="cloudroom", approval="yes-123"))
    assert cloud.started == ["move me"]


def test_always_on_loops_never_move(runtimes):
    runtimes.register(FakeCloud(runtimes.local.store))
    runtimes.approval_check = lambda approval, spec: True
    with pytest.raises(RuntimeRefused, match="always"):
        runtimes.start(JobSpec("reminders", "Reminders loop", func=lambda: None, service=True, runtime="cloudroom",
                               approval="yes"))


# -- item 3: everything that starts M5 jobs goes through the runtime ------------------------------------------------
def test_nothing_outside_the_runtime_starts_m5_jobs_directly():
    assert find_direct_starts(ROOT) == []


def test_the_check_catches_each_direct_way(tmp_path):
    pkg = tmp_path / "prometheus"
    pkg.mkdir()
    (pkg / "x.py").write_text(
        "from prometheus.jobs.runner import start_job\n"
        "from prometheus.jobs.runner import get_default_runner as g\n"
        "from prometheus.jobs.graph import GraphRunner\n"
        "import prometheus.jobs.runner as r\n"
        "r.JobRunner(None).run_existing(1, print)\n")
    assert len(find_direct_starts(tmp_path)) == 5


def test_module_start_job_uses_the_runtime(runtimes, monkeypatch):
    from prometheus import runtime

    seen = []
    monkeypatch.setattr(runtimes.local, "start", lambda spec: seen.append(spec) or runtimes.local.store.create_job(
        spec.kind, spec.title))
    runtime.set_runtimes(runtimes)
    runtime.start_job("k", "t", lambda: None, name="n", service=True)
    assert seen and seen[0].kind == "k" and seen[0].service is True


def test_the_app_wires_one_runtime_for_jobs_and_graphs(app_client):
    _, services = app_client()
    from prometheus import runtime

    assert isinstance(services.runtime, Runtimes) and runtime.get_runtimes() is services.runtime
    assert services.orchestrator.runtime is services.runtime.local
    assert services.orchestrator.graphs is services.runtime.local.graphs
    assert services.jobs is services.runtime.local.store


def test_the_orchestrator_starts_its_graphs_through_the_runtime():
    from prometheus.orchestrator.planner import Orchestrator

    src = inspect.getsource(Orchestrator._execute_graph)
    assert "start_graph" in src and ".create(" not in src and "graphs.start(" not in src
