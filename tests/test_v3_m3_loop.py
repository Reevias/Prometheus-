"""M3.3: the think-act-look loop for complex jobs."""
from __future__ import annotations

import json

from prometheus.agents.base import Agent, StepResult
from prometheus.models.base import ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.loop import ThinkActLoop
from prometheus.orchestrator.planner import Orchestrator
from test_orchestrator import PROMPTS

COMPLEX = "find my physics notes, summarise this file, then make slides and email them to Ravi"


def act(agent, goal, inputs=None, thought="next"):
    return json.dumps({"thought": thought, "done": False, "answer": "", "agent": agent, "goal": goal, "kind": "",
                       "inputs": [{"key": k, "value": v} for k, v in (inputs or {}).items()]})


def done(answer, thought="all done"):
    return json.dumps({"thought": thought, "done": True, "answer": answer, "agent": "", "goal": "", "kind": "",
                       "inputs": []})


class Finder(Agent):
    name, description, tools = "finder", "finds notes", set()

    def run(self, goal, inputs, ctx):
        return StepResult("done", "found notes.pdf", {"paths": ["notes.pdf"]})


class Echo(Agent):
    name, description, tools = "echo", "uses a path", set()
    got = []

    def run(self, goal, inputs, ctx):
        Echo.got.append(inputs.get("path"))
        return StepResult("done", f"used {inputs.get('path')}")


class Secret(Agent):
    name, description, tools, private_outputs = "secret", "reads a private file", set(), True

    def run(self, goal, inputs, ctx):
        return StepResult("done", "DIARY: owes 5000")


REG = {"finder": Finder, "echo": Echo, "secret": Secret}


def setup(make_ctx, replies, local=None, rounds=12):
    cloud = FakeProvider(replies, name="cloud", is_local=False)
    cloud.is_free = True
    providers = [cloud] + ([local] if local else [])
    ctx = make_ctx(providers=providers, routes={"plan": ["cloud"], "combine": ["cloud"], "research": ["cloud"],
                                                "private": [local.name] if local else []})
    o = Orchestrator(ctx.store, ctx.router, REG, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    o.front_door, o.loop = True, ThinkActLoop(o, max_rounds=rounds)
    return o, ctx, cloud


def test_two_actions_then_the_answer_with_visible_rounds(make_ctx):
    Echo.got.clear()
    o, ctx, cloud = setup(make_ctx, [act("finder", "find notes"), act("echo", "use it", {"path": "$round1.paths"}),
                                     done("Slides made and sent.")])
    out = o.run(ctx.store.create_chat("t"), COMPLEX)
    assert out.status == "done" and out.answer == "Slides made and sent."
    steps = ctx.store.steps(out.task_id)
    assert [s.goal for s in steps] == ["Round 1: find notes", "Round 2: use it"]
    assert Echo.got == [["notes.pdf"]]                          # the round-1 result was passed on
    assert "Round 1: next" in str(ctx.store.agent_notes(out.task_id))


def test_it_stops_after_the_round_limit_and_writes_what_it_has(make_ctx):
    o, ctx, cloud = setup(make_ctx, [act("finder", "again")] * 3 + [done("Partial answer.")], rounds=3)
    out = o.run(ctx.store.create_chat("t"), COMPLEX)
    assert out.answer == "Partial answer." and len(ctx.store.steps(out.task_id)) == 3


def test_stop_ends_it_before_the_next_round(make_ctx):
    o, ctx, cloud = setup(make_ctx, [act("finder", "find"), act("finder", "find again"), done("x")])
    chat = ctx.store.create_chat("t")
    orig = Finder.run

    def stop_after(self, goal, inputs, c):
        o.stopping.add(chat)
        return orig(self, goal, inputs, c)
    Finder.run = stop_after
    try:
        out = o.run(chat, COMPLEX)
    finally:
        Finder.run = orig
    assert out.status == "stopped" and len(ctx.store.steps(out.task_id)) == 1


def test_after_reading_your_files_only_the_laptop_ai_thinks(make_ctx):
    local = FakeProvider([done("Local answer.")], name="local")
    o, ctx, cloud = setup(make_ctx, [act("secret", "read diary")], local=local)
    out = o.run(ctx.store.create_chat("t"), COMPLEX)
    assert out.answer == "Local answer."
    assert "DIARY" not in json.dumps(cloud.calls) and "DIARY" in json.dumps(local.calls)


class DownLocal(FakeProvider):
    def complete(self, *a, **k):
        raise ProviderUnavailable("ollama down")


def test_with_no_laptop_ai_it_shows_what_it_found_and_sends_nothing(make_ctx):
    o, ctx, cloud = setup(make_ctx, [act("secret", "read diary")], local=DownLocal([""], name="local"))
    out = o.run(ctx.store.create_chat("t"), COMPLEX)
    assert out.status == "done" and "nothing was sent online" in out.answer
    assert "DIARY" not in json.dumps(cloud.calls)


def test_a_bad_reply_or_unknown_agent_costs_a_round_not_the_task(make_ctx):
    o, ctx, cloud = setup(make_ctx, ["not json", act("nope", "x"), done("Recovered.")])
    out = o.run(ctx.store.create_chat("t"), COMPLEX)
    assert out.answer == "Recovered."
