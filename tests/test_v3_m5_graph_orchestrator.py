"""M5 5.2 item 8: the M3 orchestrator hands a plan with independent steps to the task graph."""
from __future__ import annotations

import dataclasses
import json
import threading

from prometheus.agents.base import Agent, StepResult
from prometheus.config import Settings
from prometheus.jobs.graph import GraphRunner
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, FAILED
from prometheus.jobs.store import JobStore
from prometheus.models.base import ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator

MEET = threading.Barrier(2, timeout=3)
SEEN: list = []


class Meet(Agent):
    name = "meet"
    description = "waits for its twin"
    tools = set()

    def run(self, goal, inputs, ctx):
        MEET.wait()                       # both 'meet' steps must run at the same time to get past this
        return StepResult("done", f"met {goal}", {"who": goal})


class Join(Agent):
    name = "join"
    description = "joins"
    tools = set()

    def run(self, goal, inputs, ctx):
        SEEN.append(inputs)
        return StepResult("done", "joined")


class Broken(Agent):
    name = "broken"
    description = "fails"
    tools = set()

    def run(self, goal, inputs, ctx):
        raise RuntimeError("disk on fire")


class NoModel(Agent):
    name = "nomodel"
    description = "needs a model"
    tools = set()

    def run(self, goal, inputs, ctx):
        raise ProviderUnavailable("No AI model is available right now.")


REGISTRY = {c.name: c for c in (Meet, Join, Broken, NoModel)}


def step(i, agent, deps=(), goal=None, **inputs):
    return {"id": i, "agent": agent, "goal": goal or agent, "depends_on": list(deps),
            "inputs": [{"key": k, "value": v} for k, v in inputs.items()]}


def build(make_ctx, steps, parallel=True):
    MEET.reset()
    SEEN.clear()
    ctx0 = make_ctx(providers=[FakeProvider([json.dumps({"steps": steps})], name="planner"),
                               FakeProvider(["All done."], name="combiner")],
                    routes={"plan": ["planner"], "combine": ["combiner"], "private": ["planner"]})
    orch = Orchestrator(ctx0.store, ctx0.router, REGISTRY,
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch, None)
    orch.settings = Settings(parallel_steps=parallel)
    jobs = JobStore(ctx0.store)
    orch.graphs = GraphRunner(jobs, JobRunner(jobs), max_parallel=2)
    return orch, ctx0, ctx0.store.create_chat("c"), jobs


def graphs_of(jobs):
    return jobs.list_jobs(kind="task_graph")


def test_independent_steps_run_at_once_and_the_answer_is_written(make_ctx):
    orch, ctx, chat, jobs = build(make_ctx, [step(1, "meet", goal="a"), step(2, "meet", goal="b"),
                                             step(3, "join", deps=[1, 2], x="$step1.who", y="$step2.who")])
    out = orch.run(chat, "do both then join")
    assert out.status == "done" and out.answer == "All done."
    assert SEEN == [{"x": "a", "y": "b"}]
    assert [s.status for s in ctx.store.steps(out.task_id)].count("done") == 3
    [g] = graphs_of(jobs)
    assert g.state == DONE and g.payload["task_id"] == out.task_id


def test_with_the_switch_off_steps_run_one_by_one_as_before(make_ctx):
    orch, ctx, chat, jobs = build(make_ctx, [step(1, "join"), step(2, "join")], parallel=False)
    assert orch.run(chat, "x").status == "done"
    assert graphs_of(jobs) == []


def test_a_plan_that_is_one_chain_still_runs_one_by_one(make_ctx):
    orch, ctx, chat, jobs = build(make_ctx, [step(1, "join"), step(2, "join", deps=[1])])
    assert orch.run(chat, "x").status == "done"
    assert graphs_of(jobs) == []                         # nothing could run at once: no graph needed


def test_a_failed_step_skips_what_needs_it_and_the_rest_carries_on(make_ctx):
    orch, ctx, chat, jobs = build(make_ctx, [step(1, "broken"), step(2, "join", deps=[1]), step(3, "join")])
    orch.step_tries = 1
    out = orch.run(chat, "x")
    by_goal = {s.agent + str(i): s.status for i, s in enumerate(ctx.store.steps(out.task_id))}
    statuses = sorted(s.status for s in ctx.store.steps(out.task_id))
    assert statuses == ["done", "failed", "skipped"], by_goal
    assert len(SEEN) == 1 and graphs_of(jobs)[0].state == FAILED


def test_an_ai_limit_in_a_parallel_step_pauses_the_task_like_before(make_ctx):
    orch, ctx, chat, jobs = build(make_ctx, [step(1, "nomodel"), step(2, "join"), step(3, "join", deps=[1])])
    out = orch.run(chat, "x")
    assert out.status == "paused"
    assert graphs_of(jobs)[0].state == CANCELLED


class Stopper(Agent):
    name = "stopper"
    description = "presses Stop"
    tools = set()
    orch = None
    chat = None

    def run(self, goal, inputs, ctx):
        Stopper.orch.request_stop(Stopper.chat)
        return StepResult("done", "pressed stop")


def test_stop_pressed_during_a_graph_stops_the_task(make_ctx):
    REGISTRY["stopper"] = Stopper
    try:
        orch, ctx, chat, jobs = build(make_ctx, [step(1, "stopper"), step(2, "join", deps=[1]), step(3, "join", deps=[1]),
                                                 step(4, "join", deps=[2, 3])])
        Stopper.orch, Stopper.chat = orch, chat
        out = orch.run(chat, "x")
        assert out.status == "stopped"
        assert SEEN == [] and graphs_of(jobs)[0].state == CANCELLED
    finally:
        REGISTRY.pop("stopper")
