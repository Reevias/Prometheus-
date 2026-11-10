import dataclasses
import json

import pytest

from prometheus.agents.base import Agent, StepResult
from prometheus.models.base import ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import PlanError, Orchestrator, resolve_inputs, validate_plan

PROMPTS = None  # use the package prompts


class Lister(Agent):
    name = "lister"
    description = "lists"
    tools = set()

    def run(self, goal, inputs, ctx):
        return StepResult("done", "found 2 pdfs", {"pdfs": ["a.pdf", "b.pdf"]})


class Reader(Agent):
    name = "reader"
    description = "reads"
    tools = set()
    seen = []

    def run(self, goal, inputs, ctx):
        Reader.seen.append(inputs)
        return StepResult("done", f"read {len(inputs['paths'])}")


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
        raise ProviderUnavailable("No AI model is available for 'private'. Start Ollama or add an API key in Settings.")


REGISTRY = {c.name: c for c in (Lister, Reader, Broken, NoModel)}


def plan(*steps):
    return json.dumps({"steps": list(steps)})


def step(i, agent, deps=(), **inputs):
    return {"id": i, "agent": agent, "goal": agent, "inputs": [{"key": k, "value": v} for k, v in inputs.items()],
            "depends_on": list(deps)}


def build(make_ctx, planner_replies, combine="All done."):
    planner = FakeProvider(planner_replies, name="planner")
    combiner = FakeProvider([combine], name="combiner")
    ctx0 = make_ctx(providers=[planner, combiner], routes={"plan": ["planner"], "combine": ["combiner"],
                                                           "private": ["planner"]})
    orch = Orchestrator(ctx0.store, ctx0.router, REGISTRY,
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch, PROMPTS)
    chat = ctx0.store.create_chat("c")
    return orch, ctx0, chat, planner, combiner


def test_success_flow_two_steps(make_ctx):
    Reader.seen.clear()
    p = plan(step(1, "lister", folder="Downloads"), step(2, "reader", deps=[1], paths="$step1.pdfs"))
    orch, ctx, chat, _, combiner = build(make_ctx, [p])
    out = orch.run(chat, "sort and read")
    assert out.status == "done" and out.answer == "All done."
    assert Reader.seen == [{"paths": ["a.pdf", "b.pdf"]}]
    assert ctx.store.messages(chat)[-1].text == "All done."
    assert "<untrusted" in combiner.calls[0]["messages"][0]["content"]


def test_invalid_plan_retries_once_then_fails(make_ctx):
    good = plan(step(1, "lister"))
    orch, _, chat, planner, _ = build(make_ctx, ["not json", good])
    assert orch.run(chat, "x").status == "done"
    assert "not valid" in planner.calls[1]["messages"][-1]["content"].lower()
    orch2, _, chat2, _, _ = build(make_ctx, ["not json", "still not json"])
    out = orch2.run(chat2, "x")
    assert out.status == "failed" and "plan" in out.answer.lower()


def test_unknown_agent_and_forward_dependency_rejected():
    with pytest.raises(PlanError):
        validate_plan(plan(step(1, "ghost")), REGISTRY)
    with pytest.raises(PlanError):
        validate_plan(plan(step(1, "lister", deps=[2]), step(2, "lister")), REGISTRY)
    with pytest.raises(PlanError):
        validate_plan(plan(step(1, "lister"), step(1, "reader")), REGISTRY)
    with pytest.raises(PlanError):
        validate_plan(json.dumps({"steps": []}), REGISTRY)


def test_inputs_accept_object_and_json_lists():
    p = validate_plan(json.dumps({"steps": [{"id": 1, "agent": "reader", "goal": "g",
                                             "inputs": {"paths": ["x.pdf"]}, "depends_on": []}]}), REGISTRY)
    assert p.steps[0]["inputs"] == {"paths": ["x.pdf"]}
    p2 = validate_plan(plan(step(1, "reader", paths='["y.pdf"]')), REGISTRY)
    assert p2.steps[0]["inputs"] == {"paths": ["y.pdf"]}


def test_resolve_unknown_reference_fails():
    with pytest.raises(PlanError):
        resolve_inputs({"paths": "$step9.pdfs"}, {})


def test_failed_step_skips_dependents(make_ctx):
    p = plan(step(1, "broken"), step(2, "reader", deps=[1], paths="$step1.pdfs"), step(3, "lister"))
    orch, ctx, chat, _, _ = build(make_ctx, [p])
    out = orch.run(chat, "x")
    statuses = [s.status for s in ctx.store.steps(out.task_id)]
    assert statuses == ["failed", "skipped", "done"]
    assert "disk on fire" in ctx.store.steps(out.task_id)[0].result


def test_no_model_pauses_task(make_ctx):
    orch, ctx, chat, _, _ = build(make_ctx, [plan(step(1, "nomodel"))])
    out = orch.run(chat, "x")
    assert out.status == "paused" and "Start Ollama" in out.answer
    assert ctx.store.task(out.task_id)["status"] == "paused"


def test_no_planner_pauses_task(make_ctx):
    ctx0 = make_ctx()
    orch = Orchestrator(ctx0.store, ctx0.router, REGISTRY, lambda t, s: ctx0, ctx0.killswitch, PROMPTS)
    out = orch.run(ctx0.store.create_chat("c"), "x")
    assert out.status == "paused" and "No AI model" in out.answer


def test_kill_switch_stops_between_steps(make_ctx):
    class Killer(Agent):
        name = "killer"
        description = "stops"
        tools = set()

        def run(self, goal, inputs, ctx):
            ctx.killswitch.trigger("user stop")
            return StepResult("done", "ok")

    REGISTRY["killer"] = Killer
    try:
        orch, ctx, chat, _, _ = build(make_ctx, [plan(step(1, "killer"), step(2, "lister"))])
        out = orch.run(chat, "x")
        assert out.status == "stopped"
        assert [s.agent for s in ctx.store.steps(out.task_id)] == ["killer"]
    finally:
        del REGISTRY["killer"]


def test_planner_never_sees_file_contents(make_ctx, tmp_home):
    (tmp_home.home / "secret.txt").write_text("SENTINEL-1234")
    orch, _, chat, planner, _ = build(make_ctx, [plan(step(1, "lister"))])
    orch.run(chat, "sort my home folder")
    assert "SENTINEL-1234" not in json.dumps(planner.calls)
    assert "lister" in planner.calls[0]["system"]
