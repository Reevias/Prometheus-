from types import SimpleNamespace

import prometheus.agents.email_agent as email_agent
from prometheus.watchers import ALERTS_CHAT, Watchers
from tests.conftest import login
from tests.test_v14_connections import box


def alerts(store):
    chat = dict((t, c) for c, t in store.chats()).get(ALERTS_CHAT)
    return [m.text for m in store.messages(chat)] if chat else []


def test_mail_from_chosen_senders_is_told_once(make_ctx, monkeypatch):
    monkeypatch.setattr(email_agent, "MAILBOX_FACTORY", box)
    ctx = make_ctx()
    s = SimpleNamespace(email_address="me@gmail.com", email_alert_senders=["bank.com"], watch_battery=False,
                        watch_disk=False, watch_cpu=False, watch_folders=[])
    svc = SimpleNamespace(store=ctx.store, settings=s, voice=None, running={}, home=ctx.home, policy=ctx.policy)
    told = []
    w = Watchers(svc, notify=told.append)
    assert w.check_email() == ["7"]
    assert alerts(ctx.store) == ["New email from Bank: Your statement"] and told
    assert w.check_email() == []                                     # the same email is never told twice
    s.email_alert_senders = []
    assert w.check_email() == []


def test_webhook_needs_its_secret_and_understands_github(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    page = client.get("/settings").text
    token = services.settings.webhook_token
    assert token and f"/hooks/{token}" in page
    client.cookies.clear()                                           # outside apps have no login
    assert client.post("/hooks/wrong", json={"text": "hi"}).status_code == 404
    r = client.post(f"/hooks/{token}", json={"action": "completed", "repository": {"full_name": "Reevias/Prometheus-"},
                                             "workflow_run": {"name": "Tests", "conclusion": "failure",
                                                              "head_branch": "main"}},
                    headers={"X-GitHub-Event": "workflow_run"})
    assert r.status_code == 200
    assert alerts(services.store) == ["GitHub: Tests failed on Reevias/Prometheus- (main)."]
    client.post(f"/hooks/{token}", json={"source": "Doorbell", "text": "Someone is at the door"})
    assert alerts(services.store)[-1] == "Doorbell: Someone is at the door"
    login(client)
    h = {"X-CSRF-Token": client.cookies.get("csrf_token")}
    client.post("/settings/webhook/new", headers=h)
    assert services.settings.webhook_token != token
    client.cookies.clear()
    assert client.post(f"/hooks/{token}", json={"text": "old"}).status_code == 404   # the old address stops working
