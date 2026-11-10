"""v1.2: finding the right memory (stemming, partial words, ranking, recency, sources)."""
import dataclasses
import json
import sqlite3

import pytest

from conftest import login

from prometheus.agents.base import Agent, StepResult
from prometheus.memory.store import Store
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "m.db")


def texts(hits):
    return [h.text for h in hits]


def test_recall_matches_word_forms_and_partial_words(store):
    store.add_memory("My accountant is Ravi and he files my taxes in July")
    store.add_memory("I like short answers")
    assert texts(store.recall("tax return")) == ["My accountant is Ravi and he files my taxes in July"]
    assert texts(store.recall("accoun")) == ["My accountant is Ravi and he files my taxes in July"]
    assert texts(store.recall("who files things")) == ["My accountant is Ravi and he files my taxes in July"]
    assert store.recall("the of and a") == [] and store.recall("") == []


def test_recall_ranks_better_matches_first_and_respects_limit(store):
    store.add_memory("solar panels need sunlight")
    store.add_memory("my solar panel installer is Asha, her solar company is SunCo")
    store.add_memory("panel discussion on Friday")
    hits = store.recall("solar panel installer")
    assert hits[0].text.startswith("my solar panel installer")
    assert len(store.recall("solar panel installer", limit=1)) == 1


def test_recall_prefers_newer_when_equally_good(store):
    old = store.add_memory("wifi password hint is the cat's name")
    store.add_memory("wifi password hint is the dog's name")
    store._write("UPDATE memories SET created = created - 90 * 86400 WHERE id = ?", (old,))
    assert store.recall("wifi password hint")[0].text.endswith("dog's name")


def test_recall_covers_notes_and_says_where_from(store):
    chat = store.create_chat("Trip")
    task = store.create_task(chat, "x")
    store.add_memory("passport expires in 2031", source="chat: Trip")
    store.add_chat_note(chat, "passport number is in the blue folder")
    store.add_agent_note(task, "documents", "found a passport scan in Documents")
    hits = store.recall("passport")
    assert {h.kind for h in hits} == {"memory", "chat_note", "agent_note"}
    assert hits[0].kind == "memory"                                   # things you told it to remember come first
    assert {h.kind: h.source for h in hits}["memory"] == "chat: Trip"
    assert [h.kind for h in store.recall("passport", kinds=("memory",))] == ["memory"]


@pytest.mark.parametrize("query", ['"quoted" (weird) AND *', "NEAR/3 OR NOT", "a:b c^2 -d", "💡 émoji"])
def test_recall_survives_odd_queries(store, query):
    store.add_memory("quoted weird émoji things")
    store.recall(query)


def test_old_database_is_upgraded_and_searchable(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("""
        CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL);
        CREATE VIRTUAL TABLE search_index USING fts5(kind UNINDEXED, ref_id UNINDEXED, body);
        INSERT INTO memories (text, created) VALUES ('I pay my taxes in July', 1);
        INSERT INTO search_index (kind, ref_id, body) VALUES ('memory', 1, 'I pay my taxes in July');
    """)
    db.commit()
    db.close()
    store = Store(tmp_path / "old.db")
    assert texts(store.recall("tax")) == ["I pay my taxes in July"]
    assert store.memories()[0].source == ""


def test_deleted_memory_is_not_recalled(store):
    mid = store.add_memory("temporary fact about bananas")
    store.delete_memory(mid)
    assert store.recall("bananas") == []


class Noop(Agent):
    name = "noop"
    description = "n"
    tools = set()

    def run(self, goal, inputs, ctx):
        return StepResult("done", "ok")


def test_planner_gets_the_relevant_memory_not_just_the_newest(make_ctx):
    plan = json.dumps({"steps": [{"id": 1, "agent": "noop", "goal": "g", "skill": "", "inputs": [], "depends_on": []}]})
    p = FakeProvider([plan, "answer"], name="p")
    ctx0 = make_ctx(providers=[p], routes={"plan": ["p"], "combine": ["p"], "private": ["p"]})
    ctx0.store.add_memory("My tax documents live in Documents/Finance")
    for i in range(25):
        ctx0.store.add_memory(f"unrelated fact number {i} about cooking")
    orch = Orchestrator(ctx0.store, ctx0.router, {"noop": Noop},
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch)
    orch.run(ctx0.store.create_chat("c"), "find my taxes paperwork")
    sent = p.calls[0]["messages"][0]["content"]
    assert "Documents/Finance" in sent
    assert sent.count("unrelated fact") <= 5


def test_memory_page_search(app_client):
    client, services = app_client()
    login(client)
    services.store.add_memory("My accountant is Ravi")
    services.store.add_memory("I like tea")
    page = client.get("/memory?q=accountant").text
    assert "Ravi" in page and "I like tea" not in page
    full = client.get("/memory").text
    assert "Ravi" in full and "I like tea" in full and 'name="q"' in full
