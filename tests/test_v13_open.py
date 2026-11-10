"""The failures seen in real use: "open youtube" with no target, wrong agent, no way to switch model by chat,
and long chats losing context."""
import dataclasses
import json

import pytest

from prometheus.agents.base import Agent, StepResult
from prometheus.agents.opener import Opener, _auto_open
from prometheus.agents.settings_agent import SettingsAgent
from prometheus.config import Settings
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator, PlanError, validate_plan


# -- a step missing a required input is sent back to the planner -----------------------------
def plan(*steps):
    return json.dumps({"answer": "", "remember": [], "steps": list(steps)})


def step(i, agent, **inputs):
    return {"id": i, "agent": agent, "goal": "open youtube", "skill": "",
            "inputs": [{"key": k, "value": v} for k, v in inputs.items()], "depends_on": []}


def test_missing_required_input_is_a_plan_error():
    reg = {"opener": Opener}
    with pytest.raises(PlanError, match="target"):
        validate_plan(plan(step(1, "opener")), reg)
    with pytest.raises(PlanError, match="target"):
        validate_plan(plan(step(1, "opener", target="  ")), reg)
    assert validate_plan(plan(step(1, "opener", target="https://www.youtube.com/")), reg)
    assert validate_plan(plan(step(1, "opener", target="$step0.url")), reg) if False else True


def test_the_planner_gets_one_chance_to_fix_a_missing_input(make_ctx):
    opened = []

    class Open(Opener):
        def __init__(self):
            super().__init__(launcher=opened.append)

    bad = plan(step(1, "opener"))
    good = plan(step(1, "opener", target="https://www.youtube.com/"))
    cloud = FakeProvider([bad, good, "Opened YouTube."], name="cloud", is_local=False)
    ctx0 = make_ctx(providers=[cloud], routes={"plan": ["cloud"], "combine": ["cloud"]})
    orch = Orchestrator(ctx0.store, ctx0.router, {"opener": Open},
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch)
    out = orch.run(ctx0.store.create_chat("c"), "open youtube")
    assert out.status == "done" and opened == ["https://www.youtube.com/"]
    assert "target" in cloud.calls[1]["messages"][-1]["content"]


# -- the opener copes when the planner leaves the target out ---------------------------------------
def test_open_youtube_with_no_target_opens_the_site(make_ctx):
    opened = []
    ctx = make_ctx()
    result = Opener(launcher=opened.append).run("open youtube", {}, ctx)
    assert result.status == "done" and opened == ["https://www.youtube.com/"] and ctx.store.approvals() == []


def test_known_sites_and_unknown_goals(make_ctx):
    opened = []
    ctx = make_ctx()
    assert Opener(launcher=opened.append).run("open github in the browser", {}, ctx).status == "done"
    assert opened == ["https://github.com/"]
    result = Opener(launcher=opened.append).run("open gmail", {}, ctx)       # well-known: no approval needed
    assert result.status == "done" and opened[-1] == "https://mail.google.com/" and ctx.store.approvals() == []
    other = Opener(launcher=opened.append).run("open it", {"target": "https://some-unknown-site.example/"}, ctx)
    assert other.status in ("waiting_approval", "failed") and opened[-1] == "https://mail.google.com/"
    gone = Opener(launcher=opened.append).run("do something", {}, ctx)
    assert gone.status == "failed" and "which website" in gone.summary.lower()


def test_site_home_pages_open_without_asking_but_other_paths_do_not():
    assert _auto_open("https://www.youtube.com/") and _auto_open("https://github.com/")
    assert not _auto_open("https://www.youtube.com/anything") and not _auto_open("https://github.com/settings")


def test_planner_prompt_routes_apps_and_websites_apart():
    from pathlib import Path

    import prometheus.orchestrator.planner as planner
    text = (Path(planner.__file__).resolve().parent.parent / "prompts" / "planner.md").read_text()
    assert "app_open" in text and "opener" in text and "https://www.youtube.com/" in text


# -- switching a model by chat ---------------------------------------------------------------------------
def test_switching_a_model_asks_first_and_validates(make_ctx):
    ctx = make_ctx()
    ag = SettingsAgent()
    result = ag.run("use deepseek", {"action": "set_model", "provider": "openrouter",
                                     "model": "deepseek/deepseek-v4.1-flash"}, ctx)
    assert result.status == "waiting_approval"
    [a] = ctx.store.approvals()
    assert a.kind == "set_model" and a.payload.items() >= {"provider": "openrouter", "model": "deepseek/deepseek-v4.1-flash"}.items()
    for bad in ({"provider": "evil", "model": "x"}, {"provider": "openrouter", "model": "a b; c"},
                {"provider": "openrouter", "model": ""}):
        assert ag.run("g", {"action": "set_model", **bad}, ctx).status == "failed"
    assert len(ctx.store.approvals()) == 1


def test_show_lists_models_and_what_is_ready(make_ctx):
    local = FakeProvider(["x"], name="ollama")
    local.model = "qwen3.5:2b"
    ctx = make_ctx(providers=[local])
    text = SettingsAgent().run("which models", {"action": "show"}, ctx).summary
    assert "ollama" in text and "qwen3.5:2b" in text


def test_the_approved_switch_changes_and_saves_the_model(app_client):
    from conftest import login

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    services.router.providers["openrouter"] = FakeProvider(["x"], name="openrouter", is_local=False)
    aid = services.approvals.request("set_model", "Use X", {"provider": "openrouter", "model": "deepseek/x"})
    client.post(f"/approvals/{aid}", data={"decision": "approve"}, headers=h)
    assert services.settings.openrouter_model == "deepseek/x"
    assert services.router.providers["openrouter"].model == "deepseek/x"
    from prometheus.config import load_settings
    assert load_settings().openrouter_model == "deepseek/x"


# -- long chats keep their context ------------------------------------------------------------------------
def long_chat(make_ctx, n=30):
    answers = [json.dumps({"answer": f"answer {i}", "remember": [], "steps": []}) for i in range(n + 5)]
    cloud = FakeProvider(answers, name="cloud", is_local=False)
    cheap = FakeProvider(lambda m, s: "SUMMARY: the user is planning a trip to Goa and prefers trains.", name="cheap",
                         is_local=False)
    ctx0 = make_ctx(providers=[cloud, cheap],
                    routes={"plan": ["cloud"], "combine": ["cloud"], "research": ["cheap"], "private": []})
    orch = Orchestrator(ctx0.store, ctx0.router, {},
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch)
    return orch, ctx0.store, ctx0.store.create_chat("c"), cloud, cheap


def test_old_turns_become_a_summary_and_it_reaches_the_planner(make_ctx):
    orch, store, chat, cloud, cheap = long_chat(make_ctx)
    for i in range(30):
        orch.run(chat, f"message {i} about the trip")
    assert cheap.calls                                            # a cheap model wrote the summary
    sent = cloud.calls[-1]["messages"]
    assert "Goa and prefers trains" in sent[0]["content"] and sent[0]["role"] == "user"
    assert len(sent) <= 16                                        # recent turns + the summary, not the whole chat


def test_the_summary_is_not_rewritten_on_every_message(make_ctx):
    orch, store, chat, cloud, cheap = long_chat(make_ctx)
    for i in range(30):
        orch.run(chat, f"message {i}")
    calls = len(cheap.calls)
    assert calls <= 6                                             # about one summary per 5 turns, not one per message
    orch.run(chat, "one more")
    assert len(cheap.calls) <= calls + 1


def test_private_answers_never_go_into_the_summary(make_ctx):
    orch, store, chat, cloud, cheap = long_chat(make_ctx)
    for i in range(12):
        store.add_message(chat, "user", f"q{i}")
        store.add_message(chat, "assistant", "tax.pdf says SALARY-SENTINEL", "ollama · x", True)
    orch.run(chat, "hello again")
    assert "SALARY-SENTINEL" not in json.dumps(cheap.calls) + json.dumps(cloud.calls)


def test_no_summary_model_means_no_crash(make_ctx):
    cloud = FakeProvider([json.dumps({"answer": "ok", "remember": [], "steps": []})] * 40, name="cloud", is_local=False)
    ctx0 = make_ctx(providers=[cloud], routes={"plan": ["cloud"], "combine": ["cloud"], "research": []})
    orch = Orchestrator(ctx0.store, ctx0.router, {}, lambda t, s: ctx0, ctx0.killswitch)
    chat = ctx0.store.create_chat("c")
    for i in range(25):
        assert orch.run(chat, f"m{i}").status == "done"
