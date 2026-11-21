"""Bugs from the owner's laptop screenshots (21 Nov 2026). Each test failed before its fix."""
from __future__ import annotations

from prometheus.orchestrator.loop import approval_resolved
from prometheus.orchestrator.planner import paused_run
from test_v3_m3_audit_b import build
from test_v3_m3_loop import COMPLEX, act, done


def test_dont_allow_ends_the_waiting_task_instead_of_asking_again(make_ctx):
    """Pressing Don't allow made the loop carry on, and its AI asked for the same script again: a new card that looked
    exactly like the old one, so the button seemed to do nothing. A no now ends that task."""
    o, ctx, cloud = build(make_ctx, [act("asker", "send it"), act("asker", "send it"), done("x")])
    chat = ctx.store.create_chat("t")
    out = o.run(chat, COMPLEX)
    assert out.status == "paused"
    [approval] = ctx.store.approvals()
    ready = []
    ctx.approvals.on_resolved.append(lambda i, st, res: ready.extend(approval_resolved(ctx.store, i, st, res)))
    ctx.approvals.resolve(approval.id, False)
    assert ready == []                                               # nothing carries on after a no
    assert paused_run(ctx.store, out.task_id) is None
    assert ctx.store.task(out.task_id)["status"] == "stopped"
    assert ctx.store.approval(approval.id).status == "rejected"
    assert ctx.store.approvals() == []                               # and nothing new is asked
    assert len(cloud.calls) == 1


def test_one_microphone_button_in_the_top_bar(app_client, monkeypatch):
    """Two identical microphone icons sat next to Stop all: Voice on/off (#mic-toggle) and the older Mute (#mute),
    shown while voice listens. When voice is set up there is one button; Ctrl+Shift+M still mutes."""
    import re

    from conftest import login

    client, services = app_client()
    login(client)
    monkeypatch.setattr(services.voice, "ready", lambda: True)
    page = client.get("/").text
    tools = page.split('class="topbar-tools"', 1)[1].split("</header>", 1)[0]
    assert 'id="mic-toggle"' in tools
    assert len(re.findall(r'<button[^>]*class="icon-btn[^"]*(mic-toggle|mute)', tools)) == 1


def test_the_skills_library_key_can_be_entered_in_settings(app_client):
    """M4.5 added the "GitHub skills library" key, but Settings → Connections → Other keys only offered the GitHub
    key, so the owner couldn't find anywhere to paste it."""
    from conftest import login

    client, services = app_client()
    login(client)
    page = client.get("/settings").text
    keys = page.split('id="other-keys"', 1)[1].split("</section>", 1)[0]
    assert 'value="SKILLS_LIBRARY_TOKEN"' in keys and "GitHub skills library" in keys
    assert 'value="GITHUB_TOKEN"' in keys
