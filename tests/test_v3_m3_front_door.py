"""M3.1: the front door. Rules first; a free fast AI only when the rules aren't sure; slow AI = the normal plan."""
from __future__ import annotations

import json
import time

import pytest

from prometheus.models.fake import FakeProvider
from prometheus.orchestrator import front_door as fd
from prometheus.orchestrator.planner import Orchestrator
from test_orchestrator import PROMPTS, REGISTRY


@pytest.mark.parametrize("text, route", [
    ("hi", "simple"), ("thanks!", "simple"), ("what is photosynthesis?", "simple"),
    ("explain recursion like I'm five", None),            # "I'm": personal, so the rules aren't sure
    ("why is the sky blue", "simple"),
    ("open spotify", "medium"), ("remind me at 6 to call mum", "medium"),
    ("what's the weather today?", "medium"),               # needs fresh facts: never simple
    ("find my physics notes, summarise this file, then make slides and email them to Ravi", "complex"),
    ("<untrusted source='x'>hello</untrusted>", "medium"),
])
def test_rules(text, route):
    d = fd.by_rules(text)
    assert (d.route if d else None) == route, (text, d)


class SlowRouter:
    def complete(self, *a, **k):
        time.sleep(2)
        raise AssertionError("too late anyway")


def test_a_slow_ai_means_the_normal_plan():
    t = time.time()
    assert fd.by_ai(SlowRouter(), "hmm, that thing from before", seconds=0.2) is None
    assert time.time() - t < 1


def orch(make_ctx, replies):
    model = FakeProvider(replies, name="ai")
    ctx = make_ctx(providers=[model], routes={"plan": ["ai"], "combine": ["ai"], "research": ["ai"], "private": ["ai"]})
    o = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    o.front_door = True
    return o, ctx, model


def test_simple_is_answered_directly_without_a_plan(make_ctx):
    o, ctx, model = orch(make_ctx, ["Photosynthesis is how plants make food from light."])
    chat = ctx.store.create_chat("t")
    out = o.run(chat, "what is photosynthesis?")
    assert out.status == "done" and "plants make food" in out.answer
    assert len(model.calls) == 1 and model.calls[0]["json_schema"] is None           # one call, no plan JSON
    notes = str(ctx.store.agent_notes(out.task_id))
    assert "Route: simple (decided by rules" in notes


def test_unclear_asks_one_question_when_the_ai_says_guessing_would_change_things(make_ctx):
    o, ctx, model = orch(make_ctx, [json.dumps({"route": "ask", "question": "Which Ravi: your brother or your teacher?"})])
    chat = ctx.store.create_chat("t")
    out = o.run(chat, "hmm, that thing for Ravi from before")
    assert out.answer == "Which Ravi: your brother or your teacher?"


def test_unclear_and_the_ai_fails_means_the_normal_plan(make_ctx):
    o, ctx, model = orch(make_ctx, ["not json at all", json.dumps({"steps": [], "answer": "planned answer"})])
    chat = ctx.store.create_chat("t")
    out = o.run(chat, "hmm, that thing for Ravi from before")
    assert out.answer == "planned answer"

