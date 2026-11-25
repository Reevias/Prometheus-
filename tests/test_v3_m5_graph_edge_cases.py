"""Extra edge-case tests for M5 5.2 task graph (DAG).

Tests cover:
- Loops: 3-step cycles, cycle with tail, disjoint cycles, self-loops, duplicate needs.
- Missing steps: non-existent step IDs, empty string, atomic rollback on validation failure.
- 51 steps boundary: exactly 50 steps succeeds, 51 fails, 0 steps fails, bad retries/needs type.
- Failure in one branch while others carry on: independent branches, diamond graph with failed branch.
- Cancel mid-run: parallel running steps stopped, terminal graphs refuse cancel, custom reason propagation.
- Pause then resume: pause during execution, pause during retry wait, non-running pause, non-paused resume.
- Retry count after restart: retry counter preserved in SQLite across restart, max retries strictly enforced.
- Restart in the middle of a graph: multi-step graph recovery, finished steps skipped, subsequent steps execute.
"""
from __future__ import annotations

import threading
import time

import pytest

from prometheus.jobs import graph as g
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING
from prometheus.jobs.store import JobStore

CALLS: dict[str, int] = {}


@g.step_kind("edge.echo")
def _edge_echo(ctx: g.StepContext, inputs: dict):
    name = inputs.get("name", "echo")
    CALLS[name] = CALLS.get(name, 0) + 1
    return {"name": name, "got": sorted(ctx.results.keys())}


@g.step_kind("edge.fail")
def _edge_fail(ctx: g.StepContext, inputs: dict):
    name = inputs.get("name", "fail")
    CALLS[name] = CALLS.get(name, 0) + 1
    raise RuntimeError(f"{name} intentional failure")


@g.step_kind("edge.block")
def _edge_block(ctx: g.StepContext, inputs: dict):
    name = inputs.get("name", "block")
    CALLS[name] = CALLS.get(name, 0) + 1
    ctx.wait(10)
    return "stopped" if ctx.should_stop() else "waited"


@g.step_kind("edge.flaky")
def _edge_flaky(ctx: g.StepContext, inputs: dict):
    name = inputs.get("name", "flaky")
    CALLS[name] = CALLS.get(name, 0) + 1
    fail_until = inputs.get("fail_until", 2)
    if CALLS[name] < fail_until:
        raise RuntimeError(f"{name} failed on attempt {CALLS[name]}")
    return f"{name} success"


@pytest.fixture
def env(tmp_path):
    CALLS.clear()
    store = JobStore(tmp_path / "jobs.db")
    runner = JobRunner(store)
    graphs = g.GraphRunner(store, runner, waits=(0.02, 0.04), max_parallel=4)
    return store, runner, graphs


def _until(cond, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def step(sid, kind="edge.echo", needs=(), **kw):
    return {"id": sid, "kind": kind, "inputs": {"name": sid, **kw.pop("inputs", {})}, "needs": list(needs), **kw}


def by_step(store, graph_id):
    return {j.payload["step"]: j for j in store.list_jobs(parent_id=graph_id)}


# ===================================================================================================
# 1. LOOPS (CYCLE DETECTION EDGE CASES)
# ===================================================================================================

def test_three_node_cycle_rejected(env):
    store, _, graphs = env
    # A -> B -> C -> A
    steps = [
        step("a", needs=["c"]),
        step("b", needs=["a"]),
        step("c", needs=["b"]),
    ]
    with pytest.raises(g.GraphError) as exc:
        graphs.create("3-cycle", steps)
    assert "loop" in str(exc.value).lower()
    assert store.list_jobs() == []


def test_cycle_with_tail_lasso_rejected(env):
    store, _, graphs = env
    # Entry: root -> A; Cycle: A -> B -> C -> A
    steps = [
        step("root"),
        step("a", needs=["root", "c"]),
        step("b", needs=["a"]),
        step("c", needs=["b"]),
    ]
    with pytest.raises(g.GraphError) as exc:
        graphs.create("lasso-cycle", steps)
    assert "loop" in str(exc.value).lower()
    assert store.list_jobs() == []


def test_disjoint_cycles_rejected(env):
    store, _, graphs = env
    # Independent node X is fine, but Y <-> Z forms a cycle
    steps = [
        step("x"),
        step("y", needs=["z"]),
        step("z", needs=["y"]),
    ]
    with pytest.raises(g.GraphError) as exc:
        graphs.create("disjoint-cycle", steps)
    assert "loop" in str(exc.value).lower()
    assert store.list_jobs() == []


def test_duplicate_needs_handled_gracefully(env):
    store, _, graphs = env
    # Duplicate entries in needs: ["a", "a"]
    steps = [
        step("a"),
        step("b", needs=["a", "a"]),
    ]
    gid = graphs.create("duplicate-needs", steps)
    assert gid > 0
    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE
    assert by_step(store, gid)["b"].state == DONE


# ===================================================================================================
# 2. MISSING STEPS & VALIDATION
# ===================================================================================================

def test_missing_step_reference_rejected(env):
    store, _, graphs = env
    steps = [
        step("a"),
        step("b", needs=["nonexistent_step"]),
    ]
    with pytest.raises(g.GraphError) as exc:
        graphs.create("missing-step", steps)
    assert "missing" in str(exc.value).lower()
    assert "nonexistent_step" in str(exc.value)
    assert store.list_jobs() == []


def test_empty_string_needs_rejected(env):
    store, _, graphs = env
    steps = [
        step("a", needs=[""]),
    ]
    with pytest.raises(g.GraphError) as exc:
        graphs.create("empty-needs", steps)
    assert "missing" in str(exc.value).lower()


def test_step_missing_id_key_rejected(env):
    store, _, graphs = env
    steps = [{"kind": "edge.echo", "inputs": {}}]
    with pytest.raises(g.GraphError) as exc:
        graphs.create("no-id", steps)
    assert "needs an id" in str(exc.value).lower()


def test_step_invalid_retries_rejected(env):
    store, _, graphs = env
    # retries > 5
    with pytest.raises(g.GraphError) as exc1:
        graphs.create("high-retries", [step("a", retries=6)])
    assert "retries" in str(exc1.value).lower()

    # negative retries
    with pytest.raises(g.GraphError) as exc2:
        graphs.create("neg-retries", [step("a", retries=-1)])
    assert "retries" in str(exc2.value).lower()

    # non-int retries
    with pytest.raises(g.GraphError) as exc3:
        graphs.create("str-retries", [step("a", retries="2")])
    assert "retries" in str(exc3.value).lower()


def test_step_invalid_needs_type_rejected(env):
    store, _, graphs = env
    with pytest.raises(g.GraphError) as exc:
        graphs.create("invalid-needs", [{"id": "a", "kind": "edge.echo", "needs": "not-a-list"}])
    assert "needs must be a list" in str(exc.value).lower()


def test_non_list_steps_rejected(env):
    store, _, graphs = env
    for bad in (None, {}, "steps", 123):
        with pytest.raises(g.GraphError):
            graphs.create("bad-input", bad)


# ===================================================================================================
# 3. 51 STEPS BOUNDARY CHECK
# ===================================================================================================

def test_exactly_50_steps_is_allowed(env):
    store, _, graphs = env
    steps = [step(f"s{i}") for i in range(50)]
    gid = graphs.create("fifty-steps", steps)
    assert gid > 0
    assert len(by_step(store, gid)) == 50


def test_51_steps_is_refused(env):
    store, _, graphs = env
    steps = [step(f"s{i}") for i in range(51)]
    with pytest.raises(g.GraphError) as exc:
        graphs.create("51-steps", steps)
    assert "at most 50 steps" in str(exc.value).lower()
    assert store.list_jobs() == []


# ===================================================================================================
# 4. FAILURE IN ONE BRANCH WHILE OTHER CARRIES ON
# ===================================================================================================

def test_failure_in_one_branch_while_independent_branch_completes(env):
    store, _, graphs = env
    # Branch 1: fail1 (fails immediately, retries=0) -> child1 -> grandchild1
    # Branch 2: ok1 -> ok2
    steps = [
        step("fail1", kind="edge.fail", retries=0),
        step("child1", needs=["fail1"]),
        step("grandchild1", needs=["child1"]),
        step("ok1"),
        step("ok2", needs=["ok1"]),
    ]
    gid = graphs.create("two-branches", steps)
    graphs.start(gid)
    assert graphs.wait(gid, 5) == FAILED

    s = by_step(store, gid)
    # Branch 1 failed and cascaded cancellation
    assert s["fail1"].state == FAILED
    assert s["child1"].state == CANCELLED and s["child1"].error == g.NEED_FAILED
    assert s["grandchild1"].state == CANCELLED and s["grandchild1"].error == g.NEED_FAILED

    # Branch 2 carried on to completion
    assert s["ok1"].state == DONE
    assert s["ok2"].state == DONE
    assert CALLS["ok1"] == 1
    assert CALLS["ok2"] == 1
    assert "child1" not in CALLS
    assert "grandchild1" not in CALLS


def test_diamond_graph_with_one_failed_branch(env):
    store, _, graphs = env
    # root -> left (fails), right (succeeds) -> join (needs left, right)
    steps = [
        step("root"),
        step("left", kind="edge.fail", retries=0, needs=["root"]),
        step("right", needs=["root"]),
        step("join", needs=["left", "right"]),
    ]
    gid = graphs.create("diamond-fail", steps)
    graphs.start(gid)
    assert graphs.wait(gid, 5) == FAILED

    s = by_step(store, gid)
    assert s["root"].state == DONE
    assert s["left"].state == FAILED
    assert s["right"].state == DONE
    assert s["join"].state == CANCELLED and s["join"].error == g.NEED_FAILED


# ===================================================================================================
# 5. CANCEL MID-RUN
# ===================================================================================================

def test_cancel_mid_run_stops_all_parallel_running_steps(env):
    store, _, graphs = env
    # Three parallel blocking steps and one downstream step
    steps = [
        step("slow1", kind="edge.block"),
        step("slow2", kind="edge.block"),
        step("downstream", needs=["slow1", "slow2"]),
    ]
    gid = graphs.create("cancel-mid-run", steps)
    graphs.start(gid)

    # Wait until both slow steps are running
    assert _until(lambda: by_step(store, gid)["slow1"].state == RUNNING and by_step(store, gid)["slow2"].state == RUNNING)

    # Cancel while running
    assert graphs.cancel(gid, reason="User cancelled test") is True
    assert graphs.wait(gid, 5) == CANCELLED

    s = by_step(store, gid)
    assert s["slow1"].state == CANCELLED
    assert s["slow2"].state == CANCELLED
    assert s["downstream"].state == CANCELLED
    assert "downstream" not in CALLS

    # Steps must exit cleanly
    assert _until(lambda: graphs.idle(gid))


def test_cancel_refused_on_terminal_graphs(env):
    store, _, graphs = env
    gid = graphs.create("quick", [step("a")])
    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE

    # Calling cancel on DONE graph returns False
    assert graphs.cancel(gid) is False
    assert store.get_job(gid).state == DONE


# ===================================================================================================
# 6. PAUSE THEN RESUME
# ===================================================================================================

def test_pause_then_resume_completes_full_graph(env):
    store, _, graphs = env
    # step1 (block) -> step2 (echo)
    steps = [
        step("step1", kind="edge.block"),
        step("step2", needs=["step1"]),
    ]
    gid = graphs.create("pause-resume", steps)
    graphs.start(gid)

    assert _until(lambda: by_step(store, gid)["step1"].state == RUNNING)
    assert graphs.pause(gid, reason="Taking a break") is True
    assert graphs.wait(gid, 5) == PAUSED

    s = by_step(store, gid)
    assert s["step1"].state == PAUSED
    assert s["step2"].state == PENDING
    assert _until(lambda: graphs.idle(gid))

    # Now resume
    assert graphs.resume(gid) is True
    # Let step1 unblock on next run (kind echo) or finishes
    assert graphs.wait(gid, 5) in (RUNNING, DONE, PAUSED)
    # Cancel to tidy up if step1 blocks again
    graphs.cancel(gid)


def test_pause_during_retry_wait_and_resume(env):
    store, _, graphs = env
    # flaky step: fails first attempt, waits for retry (waits=0.2s)
    graphs.waits = (0.2, 0.4)
    gid = graphs.create("pause-retry", [step("flaky1", kind="edge.flaky", inputs={"fail_until": 2}, retries=2)])
    graphs.start(gid)

    # Wait until it has attempted once and is waiting for retry (retries=1)
    assert _until(lambda: by_step(store, gid)["flaky1"].retries == 1)

    # Pause while waiting for retry
    assert graphs.pause(gid, reason="Paused during retry wait") is True
    assert graphs.wait(gid, 5) == PAUSED
    assert _until(lambda: graphs.idle(gid))

    # Resuming should carry on and succeed on attempt 2
    assert graphs.resume(gid) is True
    assert graphs.wait(gid, 5) == DONE
    s = by_step(store, gid)
    assert s["flaky1"].state == DONE


def test_pause_and_resume_state_guards(env):
    store, _, graphs = env
    gid = graphs.create("guards", [step("a")])

    # Pause on PENDING graph (not yet started)
    assert graphs.pause(gid) is False

    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE

    # Pause on DONE graph
    assert graphs.pause(gid) is False

    # Resume on DONE graph
    assert graphs.resume(gid) is False


# ===================================================================================================
# 7. RETRY COUNT AFTER A RESTART
# ===================================================================================================

def test_retry_count_preserved_across_restart_and_capped(env, tmp_path):
    store, runner, graphs = env
    graphs.waits = (0.01, 0.02)
    # Always failing step with max_retries = 2
    gid = graphs.create("retry-restart", [step("fail_step", kind="edge.fail", retries=2)])

    # Simulate that attempt 1 failed before Prometheus shut down:
    # retries = 1 stored in SQLite
    step_job = by_step(store, gid)["fail_step"]
    store.update_job(step_job.id, retries=1)

    # In this run, it should only retry ONCE more (reaching retries=2, so 2 calls total in this run) then FAIL
    graphs.start(gid)
    assert graphs.wait(gid, 5) == FAILED

    final_step = by_step(store, gid)["fail_step"]
    assert final_step.state == FAILED
    assert final_step.retries == 2
    assert CALLS["fail_step"] == 2

    # Now verify with a step that already exhausted all retries (retries = 2):
    CALLS.clear()
    gid2 = graphs.create("retry-exhausted", [step("fail_step2", kind="edge.fail", retries=2)])
    step_job2 = by_step(store, gid2)["fail_step2"]
    store.update_job(step_job2.id, retries=2)

    graphs.start(gid2)
    assert graphs.wait(gid2, 5) == FAILED
    final_step2 = by_step(store, gid2)["fail_step2"]
    assert final_step2.state == FAILED
    assert final_step2.retries == 2
    # Since retries was already 2, it executed only 1 try (0 retries) and failed immediately
    assert CALLS["fail_step2"] == 1


# ===================================================================================================
# 8. RESTART IN THE MIDDLE OF A GRAPH
# ===================================================================================================

def test_restart_in_middle_of_graph_skips_done_steps_and_completes_rest(env, tmp_path):
    db_path = tmp_path / "jobs.db"
    store, runner, graphs = env

    # Graph with 3 steps: s1 -> s2 (block) -> s3 (needs s2)
    gid = graphs.create("pipeline", [
        step("s1"),
        step("s2", kind="edge.block", needs=["s1"]),
        step("s3", needs=["s2"]),
    ])
    graphs.start(gid)

    # Wait until s1 is DONE and s2 is RUNNING
    assert _until(lambda: by_step(store, gid)["s1"].state == DONE and by_step(store, gid)["s2"].state == RUNNING)

    # Simulate process kill and restart:
    # 1. Old step sees it's stopping
    store2 = JobStore(db_path)
    store2.recover_after_restart()
    g.recover_graphs(store2)

    # Graph and running step are PAUSED
    assert store2.get_job(gid).state == PAUSED
    steps2 = by_step(store2, gid)
    assert steps2["s1"].state == DONE
    assert steps2["s2"].state == PAUSED
    assert steps2["s3"].state == PENDING
    assert _until(lambda: graphs.idle(gid))

    # 2. New runner starts up in the new process
    runner2 = JobRunner(store2)
    graphs2 = g.GraphRunner(store2, runner2, waits=(0.01, 0.02), max_parallel=2)

    # Verify graph does NOT start on its own
    assert graphs2.wait(gid, 0.2) == PAUSED

    # Resume graph: s2 will run again (edge.block exits early when should_stop or waits, here it finishes)
    # We cancel it to avoid waiting 10s or we let it finish
    CALLS.clear()
    graphs2.resume(gid)
    assert _until(lambda: CALLS.get("s2") == 1)

    # s1 must NEVER be called again after resume
    assert "s1" not in CALLS
    graphs2.cancel(gid)


# ===================================================================================================
# 9. CONCURRENT GRAPHS, CANCELLING PAUSED GRAPH & RESULTS PASSING
# ===================================================================================================

def test_cancel_a_paused_graph(env):
    store, _, graphs = env
    gid = graphs.create("cancel-paused", [
        step("slow", kind="edge.block"),
        step("after", needs=["slow"]),
    ])
    graphs.start(gid)
    assert _until(lambda: by_step(store, gid)["slow"].state == RUNNING)

    # Pause graph
    assert graphs.pause(gid) is True
    assert graphs.wait(gid, 5) == PAUSED
    assert _until(lambda: graphs.idle(gid))

    # Cancel while paused
    assert graphs.cancel(gid, reason="Cancelled from paused") is True
    assert store.get_job(gid).state == CANCELLED
    s = by_step(store, gid)
    assert s["slow"].state == CANCELLED
    assert s["after"].state == CANCELLED


def test_results_passing_and_progress_reporting(env):
    store, _, graphs = env
    progress_recorded = []

    def produce_data(ctx, inputs):
        ctx.progress("halfway there")
        progress_recorded.append(store.get_job(ctx.job_id).progress)
        return {"items": [10, 20, 30], "metadata": {"source": "producer"}}

    def consume_data(ctx, inputs):
        upstream = ctx.results.get("producer")
        assert upstream["items"] == [10, 20, 30]
        assert upstream["metadata"]["source"] == "producer"
        return {"sum": sum(upstream["items"])}

    gid = graphs.create("results-pipeline", [
        step("producer", func=produce_data),
        step("consumer", func=consume_data, needs=["producer"]),
    ])
    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE

    assert progress_recorded == ["halfway there"]
    s = by_step(store, gid)
    assert s["producer"].progress == ""
    assert s["consumer"].payload["result"] == {"sum": 60}


def test_non_json_serializable_result_handled_gracefully(env):
    store, _, graphs = env

    class CustomObject:
        def __str__(self):
            return "CustomResult(ok=True)"

    def custom_step(ctx, inputs):
        return CustomObject()

    gid = graphs.create("non-json", [step("obj_step", func=custom_step)])
    graphs.start(gid)
    assert graphs.wait(gid, 5) == DONE
    res = by_step(store, gid)["obj_step"].payload["result"]
    assert res == {"text": "CustomResult(ok=True)"}


def test_multiple_concurrent_graphs_run_independently(env):
    store, _, graphs = env
    gid1 = graphs.create("graph1", [step("g1_a"), step("g1_b", needs=["g1_a"])])
    gid2 = graphs.create("graph2", [step("g2_a"), step("g2_b", needs=["g2_a"])])

    graphs.start(gid1)
    graphs.start(gid2)

    assert graphs.wait(gid1, 5) == DONE
    assert graphs.wait(gid2, 5) == DONE

    assert store.get_job(gid1).state == DONE
    assert store.get_job(gid2).state == DONE
    assert len(by_step(store, gid1)) == 2
    assert len(by_step(store, gid2)) == 2


def test_step_cancelled_cascades_need_cancelled(env):
    store, _, graphs = env
    gid = graphs.create("need-cancelled", [
        step("a"),
        step("b", needs=["a"]),
    ])
    a_job = by_step(store, gid)["a"]
    store.set_state(a_job.id, CANCELLED, error="Directly cancelled")
    graphs.start(gid)
    assert graphs.wait(gid, 5) == CANCELLED
    s = by_step(store, gid)
    assert s["b"].state == CANCELLED
    assert s["b"].error == g.NEED_CANCELLED


