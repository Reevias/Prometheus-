"""M3.4: model cards. Each job kind goes to a model that fits it; cheap paid AI may take heavy jobs within limits."""
from __future__ import annotations

import json

import pytest

from prometheus.memory.store import Store
from prometheus.models.base import PaidNeedsYes
from prometheus.models.cards import Card, load_cards, rank
from prometheus.models.fake import FakeProvider
from prometheus.models.router import ModelRouter
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch


def router(tmp_path, providers, routes, cards, paid_heavy=True):
    r = ModelRouter({p.name: p for p in providers}, routes, Budget(Store(tmp_path / "m.db"), 5.0), KillSwitch())
    r.cards, r.paid_heavy, r.paid_needs_yes = cards, paid_heavy, True
    return r


def paid(name, usd=0.001):
    p = FakeProvider(["ok from " + name] * 20, name=name, is_local=False, usd=usd)
    return p


def free(name):
    p = FakeProvider(["ok from " + name] * 20, name=name, is_local=False)
    p.is_free = True
    return p


def test_shipped_cards_are_sane():
    cards, note = load_cards(None)
    assert len(cards) > 20 and "shipped" in note
    by = {c.entry: c for c in cards}
    assert by["anthropic:claude-haiku-5-5"].cheap and not by["anthropic:claude-sonnet-5-5"].cheap
    assert by["ollama"].local and by["ollama"].fits("private")
    assert all(not c.fits("private") for c in cards if not c.local)        # your files: laptop only
    assert not any(c.makes_images for c in cards)                           # no image AI yet (owner: flag only)


def test_an_image_model_never_gets_research_and_private_stays_local():
    img = Card("pix", "Picture maker", ("images", "research"), level=3, makes_images=True)
    cloud = Card("cloud", "Cloud", ("research", "private"), level=3)
    local = Card("lap", "Laptop", ("private",), level=1, local=True)
    assert rank([img, cloud, local], "research", lambda e: True) == [cloud]
    assert rank([img, cloud, local], "images", lambda e: True) == [img]
    assert rank([img, cloud, local], "private", lambda e: True) == [local]


def test_best_fit_first_and_free_wins_a_tie():
    cards = [Card("weak", "W", ("research",), 1), Card("paidstrong", "P", ("research",), 3, 0.5, 1.0),
             Card("freestrong", "F", ("research",), 3)]
    assert [c.entry for c in rank(cards, "research", lambda e: True)] == ["freestrong", "paidstrong", "weak"]
    assert [c.entry for c in rank(cards, "research", lambda e: e != "freestrong")] == ["paidstrong", "weak"]


def test_router_sends_each_job_to_a_model_that_fits(tmp_path):
    cards = [Card("coder", "Coder", ("code",), 3), Card("talker", "Talker", ("chat", "summarise"), 1)]
    r = router(tmp_path, [free("coder"), free("talker"), free("old")], {"plan": ["old"], "combine": ["old"],
                                                                        "research": ["old"]}, cards)
    assert r.complete("combine", [{"role": "user", "content": "hi"}], "s").provider == "talker"
    with r.step_kind("code"):
        assert r.complete("research", [{"role": "user", "content": "fix"}], "s").provider == "coder"
    assert r.complete("research", [{"role": "user", "content": "x"}], "s").provider == "old"   # no card: the route


def test_cheap_paid_takes_heavy_jobs_only_inside_a_task_and_under_the_cap(tmp_path):
    cards = [Card("cheap", "Cheap", ("research", "chat"), 3, 0.5, 1.0), Card("dear", "Dear", ("research",), 3, 4.0, 20.0)]
    r = router(tmp_path, [paid("cheap", usd=0.06), paid("dear")], {"research": [], "combine": []}, cards)
    msg = [{"role": "user", "content": "q"}]
    with pytest.raises(PaidNeedsYes):
        r.complete("research", msg, "s")                      # not inside a task: asks
    with r.task_spending():
        assert r.complete("research", msg, "s").provider == "cheap"
        assert r.complete("research", msg, "s").provider == "cheap"     # 0.06 spent, still under 0.10 before it
        with pytest.raises(PaidNeedsYes):
            r.complete("research", msg, "s")                  # 0.12 spent: over the task cap, asks
    with r.task_spending():
        with pytest.raises(PaidNeedsYes):
            r.complete("combine", msg, "s")                   # chat is not a heavy job
    r.paid_heavy = False
    with r.task_spending():
        with pytest.raises(PaidNeedsYes):
            r.complete("research", msg, "s")                  # the switch is off


def test_pick_only_ai_is_never_chosen_by_itself(tmp_path):
    class Info:
        pick_only = True
    p = free("coarena")
    p.info = Info()
    r = router(tmp_path, [p, free("other")], {"combine": ["other"]}, [Card("coarena", "C", ("chat",), 3)])
    assert r.complete("combine", [{"role": "user", "content": "hi"}], "s").provider == "other"


def test_your_own_cards_file_wins_and_a_broken_one_is_ignored(tmp_path):
    (tmp_path / "models.json").write_text(json.dumps({"cards": [{"entry": "mine", "kinds": ["chat"]}]}))
    cards, note = load_cards(tmp_path)
    assert [c.entry for c in cards] == ["mine"] and "your" in note
    (tmp_path / "models.json").write_text("{broken")
    cards, note = load_cards(tmp_path)
    assert len(cards) > 20 and "couldn't be read" in note


def test_planner_step_kind_reaches_the_router(make_ctx):
    from prometheus.agents.base import Agent, StepResult
    from prometheus.orchestrator.planner import Orchestrator, validate_plan
    from test_orchestrator import PROMPTS

    seen = []

    class Probe(Agent):
        name, description, tools = "probe", "probe", set()

        def run(self, goal, inputs, ctx):
            seen.append(ctx.router.kind_for("research"))
            return StepResult("done", "ok")
    plan = json.dumps({"steps": [{"id": 1, "agent": "probe", "goal": "g", "inputs": [], "depends_on": [], "kind": "code"},
                                 {"id": 2, "agent": "probe", "goal": "g", "inputs": [], "depends_on": [], "kind": "nonsense"}]})
    assert validate_plan(plan, {"probe": Probe}).steps[1]["kind"] == ""
    planner = FakeProvider([plan, "done"], name="planner")
    ctx = make_ctx(providers=[planner], routes={"plan": ["planner"], "combine": ["planner"], "research": ["planner"],
                                                "private": ["planner"]})
    orch = Orchestrator(ctx.store, ctx.router, {"probe": Probe}, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    orch.run(ctx.store.create_chat("t"), "do it")
    assert seen == ["code", "research"]


def test_cards_are_listed_in_settings_and_checkable(app_client, monkeypatch):
    from conftest import login

    client, services = app_client()
    login(client)
    page = client.get("/settings").text
    assert 'id="model-cards"' in page and "Claude Haiku 5.5" in page and "(asks first)" in page
    r = client.get("/settings/model-cards/check")
    assert r.status_code == 200 and "Claude Haiku 5.5" in r.text
