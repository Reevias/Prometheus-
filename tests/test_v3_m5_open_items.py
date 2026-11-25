"""M5: the items the audit left open (25 Nov). Each test failed before its fix."""
from __future__ import annotations

import contextvars
import threading
import time

import pytest

from prometheus.jobs import graph as g
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, RUNNING
from prometheus.jobs.store import JobStore
from prometheus.memory.store import Store
from prometheus.models.base import PaidNeedsYes
from prometheus.models.cards import Card
from prometheus.models.fake import FakeProvider
from prometheus.models.router import ModelRouter, TaskBudgetReached
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch, Stopped


class SlowPaid(FakeProvider):
    """A paid AI that takes a moment to answer, so calls made at the same time overlap."""

    def complete(self, *a, **k):
        time.sleep(0.3)
        return super().complete(*a, **k)


def router(tmp_path, provider, card):
    r = ModelRouter({provider.name: provider}, {"research": [], "combine": []},
                    Budget(Store(tmp_path / "m.db"), 5.0), KillSwitch())
    r.cards, r.paid_heavy, r.paid_needs_yes = [card], True, True
    return r


def at_once(n, call):
    """Run `call` n times at the same moment, each in a copy of this context (like steps of one task graph)."""
    out, ctx = [], contextvars.copy_context()

    def one():
        try:
            out.append(ctx.copy().run(call).provider)
        except Exception as e:                       # noqa: BLE001
            out.append(type(e).__name__)
    threads = [threading.Thread(target=one) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    return out


# -- open item 1a: paid AI used without asking can't go over the per-task cap when steps run at once ---------------
def test_calls_at_the_same_time_reserve_their_cost_against_the_task_cap(tmp_path):
    # each call could cost up to about $0.04 (its room out); the cap is $0.10, so only two may run, not three
    card = Card("cheap", "Cheap", ("research",), 3, 0.5, 1.0)
    r = router(tmp_path, SlowPaid(["ok"] * 10, name="cheap", is_local=False, usd=0.001), card)
    msg = [{"role": "user", "content": "q"}]
    with r.task_spending():
        got = at_once(3, lambda: r.complete("research", msg, "s", max_tokens=40000))
    assert got.count("cheap") == 2 and got.count("PaidNeedsYes") == 1, got


# -- open item 1b: the task's own spending limit is checked before every paid call, also side by side -------------
def test_the_task_limit_is_checked_before_every_paid_call(tmp_path):
    card = Card("cheap", "Cheap", ("research",), 3, 0.5, 1.0)
    r = router(tmp_path, FakeProvider(["ok"] * 10, name="cheap", is_local=False, usd=0.03), card)
    msg = [{"role": "user", "content": "q"}]
    with r.paid_allowed(), r.task_limit(0.05):
        r.complete("research", msg, "s", max_tokens=100)               # $0.03 spent
        r.complete("research", msg, "s", max_tokens=100)               # $0.06: this one is allowed to finish
        with pytest.raises(TaskBudgetReached) as e:
            r.complete("research", msg, "s", max_tokens=100)           # over $0.05: refused before it runs
    assert "$0.05" in str(e.value)


def test_side_by_side_calls_reserve_against_the_task_limit(tmp_path):
    card = Card("cheap", "Cheap", ("research",), 3, 0.5, 1.0)
    r = router(tmp_path, SlowPaid(["ok"] * 10, name="cheap", is_local=False, usd=0.001), card)
    msg = [{"role": "user", "content": "q"}]
    with r.paid_allowed(), r.task_limit(0.05):                         # each call may cost up to ~$0.02
        got = at_once(3, lambda: r.complete("research", msg, "s", max_tokens=20000))
    assert got.count("cheap") == 2 and got.count("TaskBudgetReached") == 1, got


def test_the_orchestrator_runs_each_task_inside_its_limit():
    import inspect

    from prometheus.orchestrator.planner import Orchestrator

    assert "task_limit" in inspect.getsource(Orchestrator.run)


# -- open item 2: a step that never checks "should I stop?" stops at its next AI call or program ------------------
def test_a_stopped_step_stops_at_its_next_ai_call(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    r = ModelRouter({"fake": FakeProvider(["ok"] * 1000, name="fake")}, {"combine": ["fake"]},
                    Budget(Store(tmp_path / "m.db"), 5.0), KillSwitch())
    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2)
    calls, out = [], {}

    def busy(ctx, inputs):                               # never asks should_stop() itself
        try:
            for _ in range(200):
                r.complete("combine", [{"role": "user", "content": "x"}], "s")
                calls.append(1)
                time.sleep(0.02)
        except Stopped as e:
            out["stopped"] = str(e)
            raise

    gid = graphs.create("busy", [{"id": "a", "func": busy}])
    graphs.start(gid)
    deadline = time.monotonic() + 5
    while len(calls) < 3 and time.monotonic() < deadline:
        time.sleep(0.01)
    graphs.cancel(gid)
    deadline = time.monotonic() + 5
    while "stopped" not in out and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "stopped" in out and len(calls) < 50
    assert store.list_jobs(parent_id=gid)[0].state == CANCELLED


def test_a_stopped_step_cannot_start_a_sandbox_program(tmp_path):
    from prometheus import sandbox

    store = JobStore(tmp_path / "jobs.db")
    graphs = g.GraphRunner(store, JobRunner(store), max_parallel=2)
    gate, out = threading.Event(), {}

    def late(ctx, inputs):
        gate.wait(5)                                      # cancelled while waiting here
        try:
            sandbox.run_locked("print('should not run')\n", sandbox.new_folder(tmp_path))
        except Stopped as e:
            out["stopped"] = str(e)

    gid = graphs.create("late", [{"id": "a", "func": late}])
    graphs.start(gid)
    time.sleep(0.2)
    graphs.cancel(gid)
    gate.set()
    deadline = time.monotonic() + 5
    while "stopped" not in out and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "stopped" in out


def test_outside_a_step_nothing_changes(tmp_path):
    r = ModelRouter({"fake": FakeProvider(["ok"], name="fake")}, {"combine": ["fake"]},
                    Budget(Store(tmp_path / "m.db"), 5.0), KillSwitch())
    assert r.complete("combine", [{"role": "user", "content": "x"}], "s").text == "ok"


# -- open item 3: removing an own agent stops its run in a chat too (not only its tracked jobs) --------------------
def test_removing_an_own_agent_stops_its_running_chat_step(make_ctx, monkeypatch):
    import json

    from prometheus import own_agents as oa
    from prometheus.agents.base import REGISTRY
    from prometheus.config import Settings

    rounds = []

    def reply(messages, system):
        rounds.append(1)
        if len(rounds) == 2:
            oa.delete(s, "mybot")                         # the owner removes it while it is working
            oa.mark_removed("mybot")
        return json.dumps({"thought": "", "done": False, "answer": "", "tool": "web_search",
                           "args": [{"key": "query", "value": "x"}]})

    ctx = make_ctx(providers=[FakeProvider(reply, name="fake")],
                   routes={"combine": ["fake"], "private": ["fake"], "plan": ["fake"]})
    s = Settings()
    oa.add(s, {"name": "mybot", "about": "x", "instructions": "x", "tools": ["web_search"]}, set(REGISTRY), set())
    oa.install(dict(REGISTRY), ctx.permissions, s, set(REGISTRY))
    monkeypatch.setattr(oa, "SEARCH", lambda q, n: [])
    res = oa.agent_class(oa.specs(s)[0])().run("x", {}, ctx)
    assert res.status == "failed" and "removed" in res.summary.lower() and len(rounds) == 2
    oa.unmark_removed("mybot")
