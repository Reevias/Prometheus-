"""M3 review (16 Nov): one test per confirmed finding; each failed before its fix."""
from __future__ import annotations

import json

import pytest

from prometheus import phone_wait
from prometheus.models.base import Reply
from prometheus.models.cards import Card
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator import front_door as fd
from prometheus.orchestrator.loop import ThinkActLoop
from prometheus.orchestrator.planner import Orchestrator
from test_orchestrator import PROMPTS, REGISTRY
from test_v3_m3_cards import free, paid, router


# 1. questions that need a tool or fresh facts are never answered directly
@pytest.mark.parametrize("text", [
    "Any new emails?", "Any reminders?", "Any new messages?", "What's in Downloads?",
    "How much disk space is left?", "Which apps are using the most memory?", "Who won the match last night?",
    "What is the score of the India game?", "What are the top headlines?", "Is it going to rain?",
    "how much is bitcoin worth now?"])
def test_tool_questions_are_not_simple(text):
    d = fd.by_rules(text)
    assert d is None or d.route != "simple", text


@pytest.mark.parametrize("text", ["what is photosynthesis?", "who was Ashoka?", "explain recursion", "hi"])
def test_general_questions_stay_simple(text):
    assert fd.by_rules(text).route == "simple"


# 2. a malformed loop reply never leaves a task "running"
def test_bad_loop_inputs_fail_the_round_not_the_task(make_ctx):
    bad = json.dumps({"thought": "x", "done": False, "answer": "", "agent": "finder", "goal": "g", "kind": "",
                      "inputs": "not a list"})
    done = json.dumps({"thought": "ok", "done": True, "answer": "Fine.", "agent": "", "goal": "", "kind": "", "inputs": []})
    model = FakeProvider([bad, done], name="ai")
    ctx = make_ctx(providers=[model], routes={"plan": ["ai"], "combine": ["ai"], "private": ["ai"]})
    o = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    o.front_door, o.loop = True, ThinkActLoop(o)
    out = o.run(ctx.store.create_chat("t"), "find my notes, summarise this file, then make slides and email them")
    assert out.status in ("done", "failed") and ctx.store.task(out.task_id)["status"] != "running"


def test_any_crash_in_the_loop_ends_the_task(make_ctx):
    model = FakeProvider(["x"], name="ai")
    ctx = make_ctx(providers=[model], routes={"plan": ["ai"], "combine": ["ai"], "private": ["ai"]})
    o = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)

    class Boom(ThinkActLoop):
        def _think(self, *a, **k):
            raise RuntimeError("surprise")
    o.front_door, o.loop = True, Boom(o)
    out = o.run(ctx.store.create_chat("t"), "find my notes, summarise this file, then make slides and email them")
    assert out.status == "failed" and ctx.store.task(out.task_id)["status"] == "failed"


# 3. phone jobs: retried at most 3 times, never dropped for a busy chat, no false "waiting for your yes"
class Back:
    target = "100.64.0.2:5555"

    def shell(self, *a, **k):
        return "ok"


def test_a_phone_job_is_tried_at_most_three_times(app_client):
    client, services = app_client()
    st = services.store
    chat = st.create_chat("x")
    services.start_task = lambda c, t, read_only=False: None
    w = phone_wait.PhoneWaiter(services, make_phone=lambda: Back())
    runs = 0
    for _ in range(6):
        if phone_wait.queue(st, chat, "alarm 6"):
            runs += w.once()
    assert runs == 3 and not phone_wait.queue(st, chat, "alarm 6")


def test_a_busy_chat_keeps_its_phone_job(app_client):
    client, services = app_client()
    st = services.store
    chat = st.create_chat("x")
    phone_wait.queue(st, chat, "alarm 6")
    services.running[chat] = object()
    w = phone_wait.PhoneWaiter(services, make_phone=lambda: Back())
    assert w.once() == 0 and len(phone_wait.jobs(st)) == 1
    services.running.pop(chat)


# 4. the cloud copy never runs the loop (laptop jobs must wait for the laptop)
def test_no_loop_on_the_cloud_copy():
    from prometheus.app import make_loop

    class S:
        role = "cloud"
    assert make_loop(object(), S()) is None
    S.role = "laptop"
    assert make_loop(object(), S()) is not None


# 5. free first for light jobs; the owner's own picks stay first
def test_light_jobs_try_free_before_paid_even_when_paid_is_stronger(tmp_path):
    cards = [Card("paidbig", "P", ("chat",), 3, 0.5, 1.0), Card("freesmall", "F", ("chat",), 1)]
    r = router(tmp_path, [paid("paidbig"), free("freesmall")], {"combine": []}, cards)
    r.paid_needs_yes = False
    assert r.complete("combine", [{"role": "user", "content": "hi"}], "s").provider == "freesmall"


def test_the_owners_research_pick_stays_first(tmp_path):
    cards = [Card("cardbest", "C", ("research",), 3)]
    r = router(tmp_path, [free("cardbest"), free("mine")], {"research": ["mine"]}, cards)
    r.pinned = {"research": "mine"}
    assert r.complete("research", [{"role": "user", "content": "q"}], "s").provider == "mine"


# 6. the front door's AI is free and never the hand-picked lead AI
def test_the_classifier_never_uses_the_lead_ai_or_paid_ai(tmp_path):
    lead = paid("lead")
    cheap = paid("cheap")
    r = router(tmp_path, [lead, cheap], {"combine": ["lead", "cheap"], "research": ["cheap"]}, [])
    r.head, r.paid_needs_yes = "lead", False
    assert fd.by_ai(r, "hmm", seconds=1) is None
    assert lead.calls == [] and cheap.calls == []


# 7. a stream with no words still has its cost counted; the cap allows for the call's own cost
def test_the_task_cap_counts_the_next_calls_cost_first(tmp_path):
    from prometheus.models.base import PaidNeedsYes

    cards = [Card("cheap", "Cheap", ("research",), 3, 1.0, 4.0)]
    r = router(tmp_path, [paid("cheap", usd=0.0)], {"research": []}, cards)
    big = [{"role": "user", "content": "x" * 400_000}]          # ~100k tokens in: about $0.10 on its own
    with r.task_spending():
        with pytest.raises(PaidNeedsYes):
            r.complete("research", big, "s", max_tokens=4000)


def test_an_openrouter_stream_without_words_keeps_its_cost(monkeypatch):
    import prometheus.models.openrouter as orp
    p = orp.OpenRouterProvider(model="m")
    monkeypatch.setattr(orp, "get_secret", lambda k: "key")
    monkeypatch.setattr("prometheus.models.streaming.openai_sse", lambda *a, **k: ("", {"cost": 0.02}, "m"))
    monkeypatch.setattr(p, "complete", lambda *a, **k: Reply(text="answer", provider="openrouter", model="m", usd=0.01))
    r = p.stream([{"role": "user", "content": "q"}], "s", on_text=lambda t: None)
    assert r.text == "answer" and abs(r.usd - 0.03) < 1e-9


# 8. an error inside a stream is a failure, not an answer
def test_a_stream_error_is_not_an_answer():
    import httpx

    from prometheus.models.base import ProviderUnavailable
    from prometheus.models.streaming import openai_sse
    body = ('data: {"choices":[{"delta":{"content":"Half"}}]}\n\n'
            'data: {"error":{"message":"upstream died"},"choices":[{"delta":{},"finish_reason":"error"}]}\n\n')
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body.encode())))
    with pytest.raises(ProviderUnavailable):
        openai_sse(http, "https://x", {"model": "m"}, {}, lambda t: None, "X")


# 9. an image model never gets a non-image job, even when a step is tagged oddly
def test_odd_step_kinds_are_ignored_and_image_models_stay_out(tmp_path):
    cards = [Card("pix", "Pix", ("images",), 3, makes_images=True), Card("ok", "OK", ("research",), 1)]
    r = router(tmp_path, [free("pix"), free("ok")], {"research": ["pix", "ok"]}, cards)
    with r.step_kind("images"):
        assert r.complete("research", [{"role": "user", "content": "q"}], "s").provider == "ok"
    assert r.complete("research", [{"role": "user", "content": "q"}], "s").provider == "ok"


# 10. the exact model's card wins over a bare provider card
def test_card_for_prefers_the_exact_model(tmp_path):
    cards = [Card("anthropic", "Any Claude", ("chat",), 2, 0.1, 0.5),
             Card("anthropic:claude-opus-5-5", "Opus", ("plan",), 3, 4.0, 20.0)]
    r = router(tmp_path, [paid("anthropic")], {}, cards)
    assert r.card_for("anthropic:claude-opus-5-5").name == "Opus"


# 11. Stop during a quick answer
def test_stop_during_a_quick_answer(make_ctx):
    model = FakeProvider(["An answer."], name="ai")
    ctx = make_ctx(providers=[model], routes={"plan": ["ai"], "combine": ["ai"], "private": ["ai"]})
    o = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    o.front_door = True
    chat = ctx.store.create_chat("t")
    orig = model.complete

    def stop_then(*a, **k):
        o.stopping.add(chat)
        return orig(*a, **k)
    model.complete = stop_then
    assert o.run(chat, "what is photosynthesis?").status == "stopped"
