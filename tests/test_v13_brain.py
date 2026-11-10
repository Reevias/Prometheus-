"""v1.3: a brain that answers directly, remembers the conversation and routes models sensibly."""
import dataclasses
import hashlib
import json
import shutil
from types import SimpleNamespace

import pytest

from prometheus.agents.base import Agent, StepResult
from prometheus.config import Settings, load_settings, save_settings
from prometheus.models.anthropic_provider import AnthropicProvider
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator, PlanError, validate_plan
from prometheus.selfedit.prompts import PromptStore

SECRET = "SALARY-9-LAKH-SENTINEL"


def reply(answer="", steps=(), remember=()):
    return json.dumps({"answer": answer, "remember": list(remember), "steps": list(steps)})


def step(i, agent, **inputs):
    return {"id": i, "agent": agent, "goal": "g", "skill": "",
            "inputs": [{"key": k, "value": v} for k, v in inputs.items()], "depends_on": []}


class Private(Agent):
    name = "documents"
    description = "reads personal documents"
    tools = set()
    private_outputs = True

    def run(self, goal, inputs, ctx):
        return StepResult("done", f"tax.pdf says {SECRET}")


class Plain(Agent):
    name = "research"
    description = "web research"
    tools = set()

    def run(self, goal, inputs, ctx):
        return StepResult("done", "solar panels make power")


def build(make_ctx, cloud_replies, local_replies=None):
    cloud = FakeProvider(cloud_replies, name="cloud", is_local=False)
    local = FakeProvider(local_replies or ["Local answer."], name="local")
    ctx0 = make_ctx(providers=[cloud, local], routes={"plan": ["cloud"], "combine": ["cloud"], "private": ["local"]})
    orch = Orchestrator(ctx0.store, ctx0.router, {"documents": Private, "research": Plain},
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch)
    return orch, ctx0.store, ctx0.store.create_chat("c"), cloud


# -- direct answers --------------------------------------------------------------
def test_plain_question_is_answered_directly_in_one_call(make_ctx):
    orch, store, chat, cloud = build(make_ctx, [reply(answer="Paris.")])
    out = orch.run(chat, "capital of France?")
    assert out.status == "done" and out.answer == "Paris."
    assert len(cloud.calls) == 1 and store.steps(out.task_id) == []
    assert store.messages(chat)[-1].text == "Paris." and store.messages(chat)[-1].model.startswith("cloud")


def test_plan_with_neither_answer_nor_steps_is_an_error():
    with pytest.raises(PlanError):
        validate_plan(reply(), {"research": Plain})
    assert validate_plan(reply(answer="Hi"), {"research": Plain}).answer == "Hi"
    assert validate_plan(json.dumps({"steps": [step(1, "research")]}), {"research": Plain}).answer == ""


def test_tool_tasks_are_still_planned_and_combined(make_ctx):
    orch, store, chat, cloud = build(make_ctx, [reply(steps=[step(1, "research")]), "Solar panels turn light into power."])
    out = orch.run(chat, "research solar")
    assert out.answer == "Solar panels turn light into power." and len(cloud.calls) == 2


# -- conversation memory --------------------------------------------------------------
def test_follow_up_sees_the_earlier_conversation(make_ctx):
    orch, store, chat, cloud = build(make_ctx, [reply(answer="Paris."), reply(answer="About 2 million people.")])
    orch.run(chat, "capital of France?")
    orch.run(chat, "and how many people live there?")
    sent = cloud.calls[1]["messages"]
    assert [m["role"] for m in sent] == ["user", "assistant", "user"]
    assert "capital of France" in sent[0]["content"] and sent[1]["content"] == "Paris."
    assert "how many people live there" in sent[2]["content"]


def test_history_is_trimmed_and_starts_with_a_user_turn(make_ctx):
    orch, store, chat, cloud = build(make_ctx, [reply(answer="ok")] * 30)
    for i in range(14):
        orch.run(chat, f"question {i} " + "x" * 3000)
    sent = cloud.calls[-1]["messages"]
    assert sent[0]["role"] == "user" and len(sent) <= 13
    assert all(len(m["content"]) <= 1600 for m in sent[:-1])


def test_private_answers_are_never_sent_back_to_the_cloud(make_ctx):
    orch, store, chat, cloud = build(
        make_ctx, [reply(steps=[step(1, "documents")]), reply(answer="Noted.")], ["Your tax file shows " + SECRET])
    orch.run(chat, "summarise my tax pdf")
    assert store.messages(chat)[-1].private is True and SECRET in store.messages(chat)[-1].text
    orch.run(chat, "thanks, what should I do next?")
    assert SECRET not in json.dumps(cloud.calls)
    assert "written on your laptop" in json.dumps(cloud.calls[-1]["messages"])


# -- remembering --------------------------------------------------------------------
def test_facts_you_ask_it_to_remember_are_saved_with_a_source(make_ctx):
    orch, store, chat, _ = build(make_ctx, [reply(answer="Got it.", remember=["My accountant is Ravi"])])
    orch.run(chat, "remember my accountant is Ravi")
    [m] = store.memories()
    assert m.text == "My accountant is Ravi" and m.source.startswith("chat:")


def test_remember_is_limited_and_cleaned(make_ctx):
    orch, store, chat, _ = build(make_ctx, [reply(answer="ok", remember=["a fact", "", "  ", "x" * 900, "b fact", "c fact", "d fact"])])
    orch.run(chat, "remember things")
    texts = [m.text for m in store.memories()]
    assert len(texts) == 3 and all(0 < len(t) <= 300 for t in texts)


# -- prompts ----------------------------------------------------------------------------
def test_prompts_no_longer_force_an_agent_or_add_filler():
    from pathlib import Path

    import prometheus.orchestrator.planner as planner
    root = Path(planner.__file__).resolve().parent.parent / "prompts"
    plan_text = (root / "planner.md").read_text().lower()
    combine_text = (root / "combine.md").read_text().lower()
    assert "closest agent" not in plan_text and "answer directly" in plan_text
    assert "history page" not in combine_text and "undo" not in combine_text
    style = (root / "style.md").read_text().lower()
    assert "one line" in style and "never" in style


def old_prompt_hashes():
    return {hashlib.sha256(b).hexdigest() for b in (b"old shipped text",)}


def test_unmodified_old_prompts_are_upgraded_but_edited_ones_are_kept(tmp_path, monkeypatch):
    import prometheus.selfedit.prompts as prompts_mod
    monkeypatch.setattr(prompts_mod, "KNOWN_OLD", {"planner": old_prompt_hashes(), "combine": set()})
    folder = tmp_path / "prompts"
    folder.mkdir()
    (folder / "planner.md").write_text("old shipped text")
    (folder / "combine.md").write_text("my own edited combine prompt")
    store = PromptStore(folder)
    assert "answer directly" in store.get("planner").lower()
    assert store.get("combine") == "my own edited combine prompt"
    assert "style" in {p.stem for p in folder.glob("*.md")}


# -- models -------------------------------------------------------------------------------
class FakeClient:
    def __init__(self):
        self.kwargs = None
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.kwargs = kw
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=1_000_000, output_tokens=1_000_000))


@pytest.mark.parametrize("model,usd,fallback", [("claude-opus-5-5", 24.0, True), ("claude-sonnet-5-5", 12.0, True),
                                                ("claude-haiku-5-5", 0.6, False)])
def test_each_claude_model_has_its_own_price_and_request_shape(tmp_home, monkeypatch, model, usd, fallback):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = FakeClient()
    r = AnthropicProvider(client=client).complete([{"role": "user", "content": "q"}], "s", model=model)
    assert r.usd == pytest.approx(usd) and r.model == model
    assert ("fallbacks" in client.kwargs) is fallback and ("betas" in client.kwargs) is fallback


def test_default_routes_put_claude_first_and_haiku_on_the_sub_tasks():
    routes = Settings().routes
    assert routes["plan"][0] == "anthropic" and routes["combine"][0] == "anthropic"
    assert routes["research"][0] == "anthropic:claude-haiku-5-5" and routes["private"] == ["ollama"]


def test_old_saved_routes_are_upgraded_to_the_new_defaults(tmp_home):
    old = Settings(routes={"plan": ["openrouter", "anthropic", "ollama"], "combine": ["openrouter", "anthropic", "ollama"],
                           "research": ["openrouter", "anthropic", "ollama"], "private": ["ollama"]})
    save_settings(old)
    assert load_settings().routes["plan"][0] == "anthropic"
    mine = Settings(routes={"plan": ["ollama"], "combine": ["ollama"], "research": ["ollama"], "private": ["ollama"]})
    save_settings(mine)
    assert load_settings().routes["plan"] == ["ollama"]          # a choice you made yourself is kept
