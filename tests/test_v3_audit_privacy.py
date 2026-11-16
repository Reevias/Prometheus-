"""Security audit (16 Nov): private data from your files must never reach a cloud AI. Each test failed before its fix."""
from __future__ import annotations

import json

from prometheus.agents.base import Agent, StepResult
from prometheus.agents.translate_agent import TranslateAgent
from prometheus.models.base import ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator
from prometheus.study import Study
from test_orchestrator import PROMPTS, REGISTRY


class Notes(Agent):
    name = "notes"
    description = "reads the owner's private file"
    tools = set()
    private_outputs = True

    def run(self, goal, inputs, ctx):
        return StepResult("done", "read notes", {"text": "PRIVATE-DIARY: I owe 5000 to X"})


class DownLocal(FakeProvider):
    def complete(self, *a, **k):
        raise ProviderUnavailable("ollama down")


def test_translate_never_falls_back_to_the_cloud_for_text_from_your_files(make_ctx):
    plan = json.dumps({"steps": [
        {"id": 1, "agent": "notes", "goal": "read", "inputs": [], "depends_on": []},
        {"id": 2, "agent": "translate", "goal": "translate", "inputs": [
            {"key": "action", "value": "text"}, {"key": "text", "value": "$step1.text"}, {"key": "to", "value": "Hindi"}],
         "depends_on": [1]}]})
    planner = FakeProvider([plan], name="planner", is_local=False)
    cloud = FakeProvider(["translated"], name="cloud", is_local=False)
    ctx = make_ctx(providers=[planner, cloud, DownLocal([""], name="local")],
                   routes={"plan": ["planner"], "combine": ["planner"], "research": ["cloud"], "private": ["local"]})
    orch = Orchestrator(ctx.store, ctx.router, {"notes": Notes, "translate": TranslateAgent}, lambda t, s: ctx,
                        ctx.killswitch, PROMPTS)
    chat = ctx.store.create_chat("t")
    orch.run(chat, "please render my journal into Hindi")
    assert "PRIVATE-DIARY" not in json.dumps(cloud.calls)


def test_words_you_typed_may_still_use_the_cloud_when_the_local_ai_is_down(make_ctx):
    from prometheus.translate import translate

    cloud = FakeProvider(["namaste"], name="cloud", is_local=False)
    ctx = make_ctx(providers=[cloud, DownLocal([""], name="local")],
                   routes={"research": ["cloud"], "private": ["local"]})
    out, _ = translate(ctx.router, "hello", "Hindi", private=True, cloud_if_needed=True)
    assert out == "namaste"


def test_quiz_answers_from_private_notes_are_stored_private(make_ctx):
    cloud = FakeProvider([json.dumps({"steps": [], "answer": "ok"})], name="cloud", is_local=False)
    local = FakeProvider(["x"], name="local")
    ctx = make_ctx(providers=[cloud, local], routes={"plan": ["cloud"], "combine": ["cloud"], "private": ["local"]})
    orch = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    chat = ctx.store.create_chat("quiz")
    study = Study(ctx.store)
    study.add("med", "SECRET-Q what is my diagnosis?", "SECRET-A hypertension stage 2")
    study.add("med", "second q", "second a")
    orch.interceptors.append(study.answer)
    ctx.store.add_message(chat, "assistant", study.start_quiz(chat), "instant", True)   # the quiz agent's own, private
    orch.run(chat, "no idea")                      # the feedback holds the card's answer
    orch.run(chat, "stop")
    cloud.calls.clear()
    orch.run(chat, "what is the capital of France")
    sent = json.dumps(cloud.calls)
    assert "SECRET-A" not in sent and "SECRET-Q" not in sent


def test_scripts_given_your_files_make_private_output():
    from prometheus.agents.code_agent import CodeAgent
    from prometheus.agents.tools_agent import ToolsAgent
    from prometheus.orchestrator.planner import step_is_private

    for agent in (CodeAgent, ToolsAgent):
        assert step_is_private(agent, {"files": ["Documents/salary.csv"]})
        assert step_is_private(agent, [{"key": "files", "value": ["a.csv"]}])
        assert not step_is_private(agent, {"code": "print(1)"}) and not step_is_private(agent, {"files": []})
