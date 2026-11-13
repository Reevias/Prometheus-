"""M2.2: the Agents page, the per-agent switch (enforced by the orchestrator, not the page) and @agent picking."""
from __future__ import annotations

import json

from conftest import login

from prometheus.features import agent_blocked, agent_on, set_agent_on


def test_agents_page_lists_every_agent_with_real_status(app_client):
    client, services = app_client()
    login(client)
    st = services.store
    chat = st.create_chat("c")
    tid = st.create_task(chat, "look something up")
    sid = st.add_step(tid, "research", "Find solar panel prices", {})
    st.finish_step(sid, "done", "Found 3 sources")
    page = client.get("/v3/agents").text
    for name in services.orchestrator.registry:
        assert f'href="/v3/agents?show={name}"' in page, name
    assert "Not used yet" in page and "1 step" in page             # research ran once; others never
    assert "Running now" not in page                                # nothing is running: no made-up activity
    assert "Offline" not in page and "Error" not in page            # statuses the app can't know aren't shown
    detail = client.get("/v3/agents?show=research").text
    assert "Find solar panel prices" in detail and "Searches the web" in detail and 'id="agent-switch"' in detail
    assert 'href="/v3/agents"' in client.get("/").text              # in the sidebar


def test_switching_an_agent_off_is_enforced(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/v3/agents/research/switch", data={}, headers=h).json() == {"agent": "research", "on": False}
    assert services.settings.agents_off == ["research"] and not agent_on(services.settings, "research")
    assert "switched off (Agents page)" in agent_blocked(services.settings, "research")
    assert "research" not in dict(services.orchestrator._usable_agents())        # the planner never sees it
    assert "Off" in client.get("/v3/agents?show=research").text
    assert client.post("/api/v3/agents/research/switch", data={"on": "1"}, headers=h).json()["on"] is True
    assert client.post("/api/v3/agents/nope/switch", data={"on": "1"}, headers=h).status_code == 404
    assert client.post("/api/v3/agents/research/switch", data={"on": "1"}).status_code in (400, 403)   # CSRF


def test_switched_off_agent_refuses_even_if_planned(app_client):
    from prometheus.models.fake import FakeProvider

    plan = json.dumps({"steps": [{"id": 1, "agent": "wikipedia", "goal": "look up", "inputs": {"query": "x"},
                                  "depends_on": []}], "answer": ""})
    client, services = app_client(providers=[FakeProvider([plan, "summary"], name="fake")])
    set_agent_on(services.settings, "wikipedia", False)
    chat = services.store.create_chat("c")
    services.orchestrator.run(chat, "tell me about x")
    steps = services.store.steps(services.store.latest_task(chat)["id"])
    assert steps and steps[0].status == "failed" and "Agents page" in steps[0].result


def test_at_mention_limits_the_planner_to_that_agent(app_client):
    from prometheus.models.fake import FakeProvider

    seen = {}

    def reply(messages, system):
        seen.setdefault("system", system)
        seen.setdefault("request", messages[-1]["content"])
        return '{"steps": [], "answer": "ok"}'

    client, services = app_client(providers=[FakeProvider(reply, name="fake")])
    chat = services.store.create_chat("c")
    services.orchestrator.run(chat, "@wikipedia who was Ada Lovelace")
    agents_part = seen["system"].split("Agents:\n", 1)[1].split("\n\n", 1)[0]
    assert agents_part.startswith("- wikipedia:") and "\n- " not in agents_part     # only that agent offered
    assert "@wikipedia" not in seen["request"].split("Request: ", 1)[1]              # the mention isn't sent on


def test_at_mention_rejects_a_plan_using_another_agent(app_client):
    from prometheus.models.fake import FakeProvider

    other = json.dumps({"steps": [{"id": 1, "agent": "research", "goal": "x", "inputs": {"query": "x"},
                                   "depends_on": []}], "answer": ""})
    client, services = app_client(providers=[FakeProvider([other] * 6, name="fake")])
    chat = services.store.create_chat("c")
    out = services.orchestrator.run(chat, "@wikipedia anything")
    assert out.status == "failed" and "couldn't work out the steps" in services.store.messages(chat)[-1].text
    assert not services.store.steps(services.store.latest_task(chat)["id"])           # research never ran


def test_at_mention_of_a_switched_off_agent_says_so(app_client):
    client, services = app_client()
    set_agent_on(services.settings, "wikipedia", False)
    chat = services.store.create_chat("c")
    services.orchestrator.run(chat, "@wikipedia anything")
    assert "can't use @wikipedia" in services.store.messages(chat)[-1].text


def test_unknown_mention_is_plain_text(app_client):
    from prometheus.models.fake import FakeProvider

    seen = {}

    def reply(messages, system):
        seen["request"] = messages[-1]["content"]
        return '{"steps": [], "answer": "ok"}'

    client, services = app_client(providers=[FakeProvider(reply, name="fake")])
    chat = services.store.create_chat("c")
    services.orchestrator.run(chat, "@everyone hello")
    assert "@everyone hello" in seen["request"]


def test_mentions_offered_in_the_command_bar(app_client):
    client, services = app_client()
    login(client)
    page = client.get("/").text
    data = json.loads(page.split('id="v3-data">', 1)[1].split("</script>", 1)[0])
    names = {m["command"] for m in data["mentions"]}
    assert "@research" in names and all(m["kind"] == "mention" for m in data["mentions"])
    set_agent_on(services.settings, "research", False)
    page = client.get("/").text
    data = json.loads(page.split('id="v3-data">', 1)[1].split("</script>", 1)[0])
    assert "@research" not in {m["command"] for m in data["mentions"]}               # switched off: not offered
