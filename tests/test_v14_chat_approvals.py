import json

import pytest

import prometheus.agents.opener as opener
from prometheus.agents.opener import Opener
from prometheus.agents.system import SystemAgent
from tests.conftest import login
from tests.test_v14_apps import FakeLaptop


@pytest.fixture
def setup(app_client, monkeypatch, public_dns):
    opened = []
    client, services = app_client()
    services.approvals.register("open_link", lambda p: (opened.append(p["url"]), f"Opened {p['url']}")[1])
    h = {"X-CSRF-Token": login(client)}
    return client, services, h, opened


@pytest.fixture
def public_dns(monkeypatch):
    import socket
    real = socket.getaddrinfo
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "",
                                                                       ("93.184.216.34", 0))]
                        if host.endswith("example.com") else real(host, *a, **k))


def ctx_for(services, chat_id):
    from prometheus.agents.base import AgentContext

    task = services.store.create_task(chat_id, "t")
    step = services.store.add_step(task, "opener", "x", {})
    return AgentContext(task_id=task, step_id=step, store=services.store, router=services.router,
                        policy=services.policy, files=services.files, permissions=services.permissions,
                        approvals=services.approvals, killswitch=services.killswitch, home=services.home)


def test_small_things_ask_in_chat_and_are_remembered_everywhere(setup):
    client, services, h, opened = setup
    a = services.store.create_chat("first")
    res = Opener(launcher=opened.append).run("open it", {"target": "https://docs.example.com/page"}, ctx_for(services, a))
    assert res.status == "waiting_approval" and "in the chat" in res.summary
    assert services.store.big_approvals() == []                       # not on the Approvals page
    page = client.get(f"/chats/{a}/messages").text
    ask = services.store.asks_for_chat(a)[0]
    assert "Always allow: Any page on docs.example.com" in page and f"/ask/{ask.id}" in page
    r = client.post(f"/ask/{ask.id}", data={"decision": "allow"}, headers=h)
    assert opened == ["https://docs.example.com/page"] and "Opened" in r.text
    # the same link in ANOTHER chat opens straight away
    b = services.store.create_chat("second")
    res = Opener(launcher=opened.append).run("open it", {"target": "https://docs.example.com/page"}, ctx_for(services, b))
    assert res.status == "done" and services.store.asks_for_chat(b) == []
    # another page on that site still asks (only the exact link was allowed)
    res = Opener(launcher=opened.append).run("x", {"target": "https://docs.example.com/other"}, ctx_for(services, b))
    assert res.status == "waiting_approval"


def test_always_allow_covers_the_whole_site_and_can_be_removed(setup):
    client, services, h, opened = setup
    a = services.store.create_chat("c")
    Opener(launcher=opened.append).run("x", {"target": "https://news.example.com/a"}, ctx_for(services, a))
    ask = services.store.asks_for_chat(a)[0]
    client.post(f"/ask/{ask.id}", data={"decision": "always"}, headers=h)
    res = Opener(launcher=opened.append).run("x", {"target": "https://news.example.com/b"}, ctx_for(services, a))
    assert res.status == "done"
    settings = client.get("/settings").text
    assert "Any page on news.example.com" in settings
    rule = services.store.allow_rules()[0]
    client.post(f"/settings/allowed/{rule['id']}/remove", headers=h)
    assert services.store.allow_rules() == []
    res = Opener(launcher=opened.append).run("x", {"target": "https://news.example.com/c"}, ctx_for(services, a))
    assert res.status == "waiting_approval"


def test_dont_allow_does_nothing_and_remembers_nothing(setup):
    client, services, h, opened = setup
    a = services.store.create_chat("c")
    Opener(launcher=opened.append).run("x", {"target": "https://shop.example.com/"}, ctx_for(services, a))
    ask = services.store.asks_for_chat(a)[0]
    r = client.post(f"/ask/{ask.id}", data={"decision": "deny"}, headers=h)
    assert opened == [] and "you said no" in r.text and services.store.allow_rules() == []
    assert client.post(f"/ask/{ask.id}", data={"decision": "allow"}).status_code == 403        # needs CSRF


def test_big_things_still_wait_on_the_approvals_page(make_ctx):
    ctx = make_ctx()
    lp = FakeLaptop(packages=[{"name": "Spotify", "id": "Spotify.Spotify", "source": "winget"}])
    SystemAgent(lp).run("x", {"action": "install_app", "target": "Spotify"}, ctx)
    SystemAgent(lp).run("x", {"action": "power", "mode": "shutdown"}, ctx)
    kinds = [a.kind for a in ctx.store.big_approvals()]
    assert kinds == ["laptop_action", "laptop_action"] and ctx.store.asks_for_chat(ctx.chat_id()) == []
    res = SystemAgent(lp).run("x", {"action": "press_keys", "keys": "enter"}, ctx)
    assert res.status == "waiting_approval" and ctx.store.asks_for_chat(ctx.chat_id())     # Enter asks in the chat


def test_approvals_page_lists_only_big_things(app_client):
    client, services = app_client()
    login(client)
    cid = services.store.create_chat("c")
    services.approvals.ask("open_link", "Open this website: https://x.example.com/", {"url": "https://x.example.com/"},
                           ("https://x.example.com/", "x.example.com"), chat_id=cid)
    services.approvals.request("clone_repo", "Clone a/b into quarantine", {"repo": "a/b"})
    page = client.get("/approvals").text
    assert "Clone a/b" in page and "x.example.com" not in page
