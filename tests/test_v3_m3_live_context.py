"""M3.6: live context: the time and counts only, never titles."""
from __future__ import annotations

import json
import time

from prometheus.models.fake import FakeProvider
from prometheus.orchestrator import live_context as lc
from prometheus.orchestrator.planner import Orchestrator
from test_orchestrator import PROMPTS, REGISTRY


def test_counts_and_never_titles(app_client):
    client, services = app_client()
    st = services.store
    chat = st.create_chat("c")
    st.add_reminder(chat, "SECRET-TITLE call the lawyer", time.time() + 60)
    st.add_reminder(chat, "another one", time.time() + 120)
    st.add_reminder(chat, "next week", time.time() + 7 * 86400)
    services.approvals.request("x", "SECRET-APPROVAL pay rent", {})
    services.running[999] = object()
    text = lc.line(services)
    services.running.pop(999)
    assert "2 reminders due today" in text and "1 task running" in text and "1 approval waiting" in text
    assert "SECRET" not in text


def test_the_ai_gets_the_line_with_every_message(make_ctx):
    model = FakeProvider([json.dumps({"steps": [], "answer": "ok"})], name="ai")
    ctx = make_ctx(providers=[model], routes={"plan": ["ai"], "combine": ["ai"], "private": ["ai"]})
    o = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    o.live_context = lambda: "Right now: 0 reminders due today; 0 tasks running."
    o.run(ctx.store.create_chat("t"), "hello there, plan my week")
    sent = json.dumps(model.calls[0]["messages"])
    assert "Right now: 0 reminders due today" in sent and "Now:" in sent


def test_a_broken_live_context_never_stops_a_request(make_ctx):
    model = FakeProvider([json.dumps({"steps": [], "answer": "ok"})], name="ai")
    ctx = make_ctx(providers=[model], routes={"plan": ["ai"], "combine": ["ai"], "private": ["ai"]})
    o = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)

    def boom():
        raise RuntimeError("no")
    o.live_context = boom
    assert o.run(ctx.store.create_chat("t"), "plan my week").answer == "ok"
