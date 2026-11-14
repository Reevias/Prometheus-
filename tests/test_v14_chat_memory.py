import json

import pytest

from prometheus.agents.base import REGISTRY
from prometheus.memory.archive import ChatArchive, normalize_topic
from prometheus.memory.store import Store
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator
from prometheus.safety.paths import PathBlocked, PathPolicy


def answer(text, topic):
    return json.dumps({"answer": text, "topic": topic, "remember": [], "steps": []})


def test_topic_names_are_short_and_reused():
    assert normalize_topic("  Weather Forecasts!! ") == "weather forecasts"
    assert normalize_topic("weather forecast", ["weather forecasts"]) == "weather forecasts"
    assert normalize_topic("the taxes", ["tax"]) == "tax"
    assert normalize_topic("a b c d e f") == "a b c d"
    assert normalize_topic("") == ""


def make(tmp_path):
    store = Store(tmp_path / "m.db")
    archive = ChatArchive(store, tmp_path / "home" / "Prometheus memory")
    store.listeners.append(archive.sync)
    return store, archive


def chat(store, title, turns):
    cid = store.create_chat(title)
    for q, a, topic in turns:
        mid = store.add_message(cid, "user", q)
        store.set_message_topic(mid, topic)
        store.add_message(cid, "assistant", a, topic=topic)
    return cid


def test_chat_split_by_topic_and_linked_across_chats(tmp_path):
    store, archive = make(tmp_path)
    a = chat(store, "Monday", [("Will it rain in Delhi?", "Light rain after 4pm.", "weather"),
                               ("Fix my python loop", "Use enumerate.", "python"),
                               ("And tomorrow's weather?", "Sunny, 31C.", "weather")])
    b = chat(store, "Trip", [("Weather in Goa this week?", "Hot and humid.", "weather")])
    assert [t for t, _ in archive.split(a)] == ["weather", "python", "weather"]
    weather = {t["topic"]: t for t in store.topics()}["weather"]
    assert weather["chats"] == 2 and weather["parts"] == 3
    # files: one per chat, one per topic, and an index
    folder = archive.folder
    chats = sorted(p.name for p in (folder / "chats").iterdir())
    assert len(chats) == 2 and any(f"({a}).md" in n for n in chats)
    topic_md = (folder / "topics" / "weather.md").read_text()
    assert "Monday" in topic_md and "Trip" in topic_md and "Weather in Goa" in topic_md
    assert "../chats/" in topic_md
    assert "[weather](topics/weather.md)" in (folder / "index.md").read_text()
    chat_md = next((folder / "chats").glob(f"* ({a}).md")).read_text()
    assert "## python" in chat_md and "Use enumerate." in chat_md
    assert b


def test_message_without_topic_joins_the_one_before(tmp_path):
    store, archive = make(tmp_path)
    cid = store.create_chat("c")
    m = store.add_message(cid, "user", "rain today?")
    store.set_message_topic(m, "weather")
    store.add_message(cid, "assistant", "yes", topic="weather")
    store.add_message(cid, "user", "and tomorrow?")           # no topic: a follow-up
    store.add_message(cid, "assistant", "no")
    assert [(t, len(ms)) for t, ms in archive.split(cid)] == [("weather", 4)]


def test_fetch_returns_only_matching_part_not_whole_chat(tmp_path):
    store, archive = make(tmp_path)
    turns = [(f"Question {i} about gardening soil", "Long answer about compost. " * 40, "gardening")
             for i in range(30)]
    turns.append(("What is my wifi router model?", "Your router is a TP-Link Archer C6.", "wifi router"))
    chat(store, "Long chat", turns)
    hits = store.search_chunks("router model", chats=True, limit=2)
    assert hits and "Archer C6" in hits[0].context
    assert all(len(h.context) <= 4100 for h in hits)          # a part, not the 30-turn chat


def test_private_answers_stay_out_of_cloud_fetches(tmp_path):
    store, archive = make(tmp_path)
    cid = store.create_chat("Taxes")
    m = store.add_message(cid, "user", "summarise my salary slip")
    store.set_message_topic(m, "tax")
    store.add_message(cid, "assistant", "Your salary is 52000.", private=True, topic="tax")
    assert store.search_chunks("salary", chats=True) == []
    assert store.search_chunks("salary", chats=True, include_private=True)


def test_rename_moves_the_file(tmp_path):
    store, archive = make(tmp_path)
    cid = chat(store, "Old name", [("hi", "hello", "greeting")])
    store.rename_chat(cid, "New name")
    archive.sync(cid)
    names = [p.name for p in (archive.folder / "chats").iterdir()]
    assert len(names) == 1 and "New name" in names[0]


def test_backfill_files_old_chats(tmp_path):
    store = Store(tmp_path / "m.db")
    cid = store.create_chat("Before v1.4")
    store.add_message(cid, "user", "old question")
    store.add_message(cid, "assistant", "old answer")
    archive = ChatArchive(store, tmp_path / "mem")
    archive.sync_all()
    assert store.topics()[0]["topic"] == "general" and list((tmp_path / "mem" / "chats").iterdir())


def test_orchestrator_tags_topic_and_fetches_earlier_chat_parts(tmp_path):
    store, archive = make(tmp_path)
    old = chat(store, "Last week", [("Weather in Delhi?", "It was 38C and dry in Delhi.", "weather")])
    seen = []

    def planner(messages, system):
        seen.append((messages[-1]["content"], system))
        return answer("Probably hot again.", "Weather")

    from prometheus.models.router import ModelRouter
    from prometheus.safety.budget import Budget
    from prometheus.safety.killswitch import KillSwitch

    ks = KillSwitch()
    router = ModelRouter({"fake": FakeProvider(planner, name="fake")}, {"plan": ["fake"], "private": ["fake"]},
                         Budget(store, 5.0), ks)
    orch = Orchestrator(store, router, REGISTRY, lambda t, s: None, ks, None)
    new = store.create_chat("Today")
    orch.run(new, "What about the weather in Delhi today?")
    content, system = seen[0]
    assert "38C" in content and "From earlier chats" in content       # the matching old part was fetched
    assert "weather" in system                                        # known topics offered for reuse
    assert [m.topic for m in store.messages(new)] == ["weather", "weather"]
    weather = {t["topic"]: t for t in store.topics()}["weather"]
    assert weather["chats"] == 2 and old


def test_agents_cannot_change_the_memory_folder(tmp_path):
    home = tmp_path / "home"
    mem = home / "Prometheus memory"
    (mem / "chats").mkdir(parents=True)
    (mem / "chats" / "a.md").write_text("x")
    policy = PathPolicy(home=home, data_dir=tmp_path / "data", code_dir=tmp_path / "code", protected=(mem,))
    assert policy.check_read(mem / "chats" / "a.md")
    with pytest.raises(PathBlocked):
        policy.check_change(mem / "chats" / "a.md", home)
    with pytest.raises(PathBlocked):
        policy.check_change(mem, home)            # sorting the home folder can't move the memory folder
    (home / "b.txt").write_text("y")
    assert policy.check_change(home / "b.txt", home)


def test_memory_page_shows_topics_and_topic_page(app_client):
    from tests.conftest import login

    client, services = app_client()
    login(client)
    cid = services.store.create_chat("Rainy day")
    m = services.store.add_message(cid, "user", "Will it rain?")
    services.store.set_message_topic(m, "weather")
    services.store.add_message(cid, "assistant", "Yes.", topic="weather")
    page = client.get("/memory?view=classic").text
    assert "weather" in page and "Prometheus memory" in page
    topic = client.get("/memory/topic?t=weather").text
    assert "Rainy day" in topic and "Will it rain?" in topic and f"/chats/{cid}" in topic
