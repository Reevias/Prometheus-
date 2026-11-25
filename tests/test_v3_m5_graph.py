"""M5 step 5.2: the task graph (DAG) on top of the job tracker."""
from __future__ import annotations

import threading
import time

import pytest

from prometheus.jobs import graph as g
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING
from prometheus.jobs.store import JobStore

CALLS: dict[str, int] = {}


@g.step_kind("test.echo")
def _echo(ctx: g.StepContext, inputs: dict):
    CALLS[inputs["name"]] = CALLS.get(inputs["name"], 0) + 1
    return {"name": inputs["name"], "got": sorted(ctx.results)}


@g.step_kind("test.fail")
def _fail(ctx: g.StepContext, inputs: dict):
    CALLS[inputs["name"]] = CALLS.get(inputs["name"], 0) + 1
    raise RuntimeError(f"{inputs['name']} broke")


@g.step_kind("test.block")
def _block(ctx: g.StepContext, inputs: dict):
    CALLS[inputs["name"]] = CALLS.get(inputs["name"], 0) + 1
    ctx.wait(10)                                        # returns early once the step is told to stop
    return "stopped" if ctx.should_stop() else "waited"


@pytest.fixture
def env(tmp_path):
    CALLS.clear()
    store = JobStore(tmp_path / "jobs.db")
    runner = JobRunner(store)
    graphs = g.GraphRunner(store, runner, waits=(0.01, 0.02), max_parallel=2)
    return store, runner, graphs


def _until(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def step(sid, kind="test.echo", needs=(), **kw):
    return {"id": sid, "kind": kind, "inputs": {"name": sid}, "needs": list(needs), **kw}


def by_step(store, graph_id):
    return {j.payload["step"]: j for j in store.list_jobs(parent_id=graph_id)}


# -- item 1: a graph is a parent job with child steps -----------------------------------------------------------
def test_a_graph_is_a_parent_job_and_its_steps_are_child_jobs(env):
    store, _, graphs = env
    gid = graphs.create("Weekly report", [step("a"), step("b", needs=["a"])])
    parent = store.get_job(gid)
    assert parent.kind == "graph" and parent.state == PENDING and parent.title == "Weekly report"
    steps = by_step(store, gid)
    assert set(steps) == {"a", "b"} and steps["b"].payload["needs"] == ["a"]
    assert all(j.state == PENDING and j.parent_id == gid for j in steps.values())


# -- item 2: checks when a graph is made ------------------------------------------------------------------------
@pytest.mark.parametrize("steps, words", [
    ([step("a", needs=["b"]), step("b", needs=["a"])], "loop"),
    ([step("a", needs=["a"])], "loop"),
    ([step("a", needs=["zzz"])], "missing"),
    ([step("a"), step("a")], "twice"),
    ([step("a", kind="no.such.kind")], "kind"),
    ([], "no steps"),
])
def test_bad_graphs_are_refused_and_nothing_is_saved(env, steps, words):
    store, _, graphs = env
    with pytest.raises(g.GraphError) as e:
        graphs.create("bad", steps)
    assert words in str(e.value).lower()
    assert store.list_jobs() == []


def test_at_most_50_steps(env):
    _, _, graphs = env
    graphs.create("ok", [step(f"s{i}") for i in range(g.MAX_STEPS)])
    with pytest.raises(g.GraphError):
        graphs.create("too big", [step(f"s{i}") for i in range(g.MAX_STEPS + 1)])


# -- item 3: ready steps run, side by side when independent ----------------------------------------------------
def test_independent_steps_run_side_by_side_and_needs_wait(env):
    store, _, graphs = env
    together = threading.Barrier(2, timeout=3)

    def meet(ctx, inputs):
        together.wait()                                # only passes if both steps run at the same time
        return inputs["name"]

    gid = graphs.create("fan out", [step("a", func=meet), step("b", func=meet), step("c", needs=["a", "b"])])
    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE
    steps = by_step(store, gid)
    assert all(j.state == DONE for j in steps.values())
    assert steps["c"].payload["result"] == {"name": "c", "got": ["a", "b"]}
    assert steps["c"].started >= max(steps["a"].finished, steps["b"].finished)


def test_never_more_than_the_limit_at_once(env):
    store, runner, _ = env
    graphs = g.GraphRunner(store, runner, waits=(0.01,), max_parallel=2)
    now, most, lock = [0], [0], threading.Lock()

    def busy(ctx, inputs):
        with lock:
            now[0] += 1
            most[0] = max(most[0], now[0])
        time.sleep(0.05)
        with lock:
            now[0] -= 1

    gid = graphs.create("five", [step(f"s{i}", func=busy) for i in range(5)])
    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE and most[0] == 2


# -- item 4: retries with waits, the count is saved ------------------------------------------------------------
def test_a_step_is_retried_with_the_waits_and_the_count_is_saved(env):
    store, _, graphs = env
    tries = []

    def flaky(ctx, inputs):
        tries.append(time.monotonic())
        if len(tries) < 3:
            raise RuntimeError("not yet")
        return "ok"

    gid = graphs.create("flaky", [step("a", func=flaky)])
    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE
    a = by_step(store, gid)["a"]
    assert a.retries == 2 and a.payload["result"] == "ok"
    assert tries[1] - tries[0] >= 0.01 and tries[2] - tries[1] >= 0.02     # waits of 10 s then 60 s, scaled down


def test_the_retry_count_survives_a_restart(env):
    store, _, graphs = env
    gid = graphs.create("x", [step("a", kind="test.fail")])
    a = by_step(store, gid)["a"]
    store.update_job(a.id, retries=1)                 # one retry was already used before Prometheus closed
    graphs.start(gid)
    assert graphs.wait(gid, 5) == FAILED
    assert CALLS["a"] == 2 and by_step(store, gid)["a"].retries == 2      # one more retry, not two


def test_the_default_waits_are_10_then_60_seconds():
    assert g.RETRY_WAITS == (10, 60) and g.DEFAULT_RETRIES == 2


# -- item 5: a failed step stops only the steps that need it ---------------------------------------------------
def test_a_failed_step_cancels_what_needs_it_and_other_branches_carry_on(env):
    store, _, graphs = env
    gid = graphs.create("branches", [step("bad", kind="test.fail", retries=0), step("child", needs=["bad"]),
                                     step("grandchild", needs=["child"]), step("other")])
    graphs.start(gid)
    assert graphs.wait(gid, 5) == FAILED
    s = by_step(store, gid)
    assert s["bad"].state == FAILED and "bad broke" in s["bad"].error
    for name in ("child", "grandchild"):
        assert s[name].state == CANCELLED and s[name].error == "Cancelled (a step it needs failed)"
    assert s["other"].state == DONE
    assert "child" not in CALLS and "grandchild" not in CALLS


# -- item 6: cancel and pause spread to the steps --------------------------------------------------------------
def test_cancelling_the_graph_cancels_every_unfinished_step(env):
    store, _, graphs = env
    gid = graphs.create("long", [step("slow", kind="test.block"), step("after", needs=["slow"])])
    graphs.start(gid)
    assert _until(lambda: by_step(store, gid)["slow"].state == RUNNING)
    graphs.cancel(gid)
    assert graphs.wait(gid, 5) == CANCELLED
    s = by_step(store, gid)
    assert s["slow"].state == CANCELLED and s["after"].state == CANCELLED
    assert _until(lambda: graphs.idle(gid))           # the running step really stopped (it was told to)
    assert "after" not in CALLS


def test_pausing_the_graph_pauses_its_running_steps(env):
    store, _, graphs = env
    gid = graphs.create("long", [step("slow", kind="test.block"), step("after", needs=["slow"])])
    graphs.start(gid)
    assert _until(lambda: by_step(store, gid)["slow"].state == RUNNING)
    graphs.pause(gid)
    assert graphs.wait(gid, 5) == PAUSED
    s = by_step(store, gid)
    assert s["slow"].state == PAUSED and s["after"].state == PENDING and "after" not in CALLS


# -- item 7: after a restart the whole graph is paused; Resume carries on --------------------------------------
def test_after_a_restart_the_graph_is_paused_and_resume_skips_finished_steps(env, tmp_path):
    store, runner, graphs = env
    gid = graphs.create("two", [step("first"), step("slow", kind="test.block", needs=["first"]),
                                step("last", needs=["slow"])])
    graphs.start(gid)
    assert _until(lambda: by_step(store, gid)["slow"].state == RUNNING)
    # Prometheus is killed here. A new process opens the same database:
    store2 = JobStore(tmp_path / "jobs.db")
    store2.recover_after_restart()
    g.recover_graphs(store2)
    assert store2.get_job(gid).state == PAUSED
    assert by_step(store2, gid)["slow"].state == PAUSED and by_step(store2, gid)["first"].state == DONE
    assert _until(lambda: graphs.idle(gid))           # the old process's step saw it was paused and stopped
    graphs2 = g.GraphRunner(store2, JobRunner(store2), waits=(0.01,), max_parallel=2)
    assert graphs2.wait(gid, 0.2) == PAUSED            # nothing started by itself
    CALLS.clear()
    graphs2.resume(gid)
    assert _until(lambda: CALLS.get("slow") == 1)
    graphs2.cancel(gid)
    assert "first" not in CALLS and CALLS["slow"] == 1


def test_a_graph_that_cannot_carry_on_is_closed_after_a_restart(env, tmp_path):
    store, _, graphs = env
    gid = graphs.create("in memory", [step("a", func=lambda ctx, inputs: ctx.wait(10))])
    graphs.start(gid)
    assert _until(lambda: by_step(store, gid)["a"].state == RUNNING)
    store2 = JobStore(tmp_path / "jobs.db")
    store2.recover_after_restart()
    g.recover_graphs(store2)
    assert store2.get_job(gid).state == CANCELLED
    assert "run it again" in store2.get_job(gid).error.lower()
    assert by_step(store2, gid)["a"].state == CANCELLED
    graphs.cancel(gid)
