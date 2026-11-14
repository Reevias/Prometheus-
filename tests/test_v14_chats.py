import json

from prometheus.agents.base import REGISTRY
from prometheus.memory.archive import ChatArchive
from prometheus.memory.store import Store
from prometheus.memory.titles import clean_title, quick_title
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator
from tests.conftest import login


def test_quick_title_drops_greetings_and_requests():
    assert quick_title("hey bro can you please tell me the weather in Delhi tomorrow?") == \
        "The weather in Delhi tomorrow"
    assert quick_title("Sort my Downloads folder and summarise any PDFs about taxes") == \
        "Sort my Downloads folder and summarise any"
    assert quick_title("https://youtu.be/x") == "New chat"
    assert quick_title("hi") == "Hi"


def test_clean_title_strips_quotes_and_labels():
    assert clean_title('Title: "Delhi weather this week."') == "Delhi weather this week"
    assert clean_title("   ") == ""
    assert len(clean_title("word " * 30)) <= 60


def orchestrator(store, title):
    from prometheus.models.router import ModelRouter
    from prometheus.safety.budget import Budget
    from prometheus.safety.killswitch import KillSwitch

    reply = json.dumps({"answer": "ok", "topic": "weather", "title": title, "remember": [], "steps": []})
    ks = KillSwitch()
    router = ModelRouter({"f": FakeProvider(lambda m, s: reply, name="f")}, {"plan": ["f"], "private": ["f"]},
                         Budget(store, 5.0), ks)
    return Orchestrator(store, router, REGISTRY, lambda t, s: None, ks, None)


def test_planner_names_new_chat_but_not_a_user_named_one(tmp_path):
    store = Store(tmp_path / "m.db")
    cid = store.create_chat("Hey can you")
    orchestrator(store, "Delhi weather this week").run(cid, "hey can you check weather")
    assert store.chat_title(cid) == "Delhi weather this week"
    mine = store.create_chat("My trip")
    store.rename_chat(mine, "My trip", by="user")
    orchestrator(store, "Something else").run(mine, "weather?")
    assert store.chat_title(mine) == "My trip"


def test_name_settles_after_two_requests(tmp_path):
    store = Store(tmp_path / "m.db")
    cid = store.create_chat("x")
    for name in ("First name", "Second name", "Third name"):
        orchestrator(store, name).run(cid, "q")
    assert store.chat_title(cid) == "Second name"


def test_delete_chat_removes_everything(tmp_path):
    store = Store(tmp_path / "m.db")
    archive = ChatArchive(store, tmp_path / "mem")
    store.listeners.append(archive.sync)
    keep = store.create_chat("Keep")
    m = store.add_message(keep, "user", "rain in Goa?")
    store.set_message_topic(m, "weather")
    store.add_message(keep, "assistant", "yes", topic="weather")
    gone = store.create_chat("Gone")
    m = store.add_message(gone, "user", "rain in Pune? zebra")
    store.set_message_topic(m, "weather")
    store.add_message(gone, "assistant", "no zebra", topic="weather")
    store.add_chat_note(gone, "zebra note")
    task = store.create_task(gone, "t")
    store.add_agent_note(task, "research", "zebra agent note")
    topics = store.delete_chat(gone)
    archive.forget(gone, topics)
    assert topics == ["weather"] and dict(store.chats()) == {keep: "Keep"}
    assert store.search_chats("zebra") == [] and store.search_chunks("zebra", chats=True, include_private=True) == []
    assert store.recall("zebra", kinds=("message", "chat_note", "agent_note")) == []
    assert [s["chat_id"] for s in store.segments()] == [keep]
    names = [p.name for p in (tmp_path / "mem" / "chats").iterdir()]
    assert len(names) == 1 and "Keep" in names[0]
    assert "Gone" not in (tmp_path / "mem" / "topics" / "weather.md").read_text()


def test_search_chats_finds_words_and_names(tmp_path):
    store = Store(tmp_path / "m.db")
    a = store.create_chat("Holiday plans")
    store.add_message(a, "user", "Which beaches in Goa are quiet?")
    b = store.create_chat("Python help")
    store.add_message(b, "user", "My loop prints the wrong numbers")
    hits = store.search_chats("beach")
    assert [h["chat_id"] for h in hits] == [a] and "\ue000beaches\ue001" in hits[0]["snippets"][0]["text"]
    assert [h["chat_id"] for h in store.search_chats("python")] == [b]          # by name
    assert store.search_chats("") == [] and store.search_chats('"*(') == []


def test_rename_delete_and_search_in_the_app(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    store = services.store
    cid = store.create_chat("Old")
    store.add_message(cid, "user", "Tell me about volcanoes in Iceland")
    store.add_message(cid, "assistant", "Iceland has about 30 active volcanoes.")
    page = client.get(f"/chats/{cid}").text
    assert "Rename" in page and "Delete" in page and f'id="m' in page

    r = client.post(f"/chats/{cid}/rename", headers={**h, "HX-Prompt": "Iceland volcanoes"})
    assert r.status_code == 204 and store.chat_title(cid) == "Iceland volcanoes"
    assert store.chat_named_by(cid) == "user"
    assert any("Iceland volcanoes" in p.name for p in (services.archive.folder / "chats").iterdir())

    found = client.get(f"/chats/search?q=volcano&current={cid}").text
    assert "Iceland volcanoes" in found and f"/chats/{cid}#m" in found
    assert "<mark>volcanoes</mark>" in found
    store.add_message(cid, "user", "<script>alert(1)</script> volcano")
    assert "<script>alert" not in client.get("/chats/search?q=volcano").text      # message text stays escaped
    assert "No chat mentions" in client.get("/chats/search?q=penguin").text
    assert "Iceland volcanoes" in client.get("/chats/search?q=").text          # empty search shows all chats

    services.running[cid] = object()
    assert client.post(f"/chats/{cid}/delete", headers=h).status_code == 409   # busy chats are kept
    services.running.pop(cid)
    r = client.post(f"/chats/{cid}/delete", headers=h)
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/v3/chats" and cid not in dict(store.chats())


def test_rename_and_delete_need_csrf(app_client):
    client, services = app_client()
    login(client)
    cid = services.store.create_chat("Safe")
    assert client.post(f"/chats/{cid}/rename", headers={"HX-Prompt": "x"}).status_code == 403
    assert client.post(f"/chats/{cid}/delete").status_code == 403
    assert cid in dict(services.store.chats())


def test_first_message_gives_quick_name(app_client):
    client, services = app_client()
    csrf = login(client)
    cid = services.store.create_chat("New chat")
    client.post(f"/chats/{cid}/send", data={"text": "hey can you find cheap flights to Goa"},
                headers={"X-CSRF-Token": csrf})
    assert services.store.chat_title(cid) == "Find cheap flights to Goa"
