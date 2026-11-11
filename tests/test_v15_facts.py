import json
import time

from prometheus.memory.facts import Facts
from tests.conftest import login


def test_links_changes_and_relevance(make_ctx):
    store = make_ctx().store
    clock = [1_780_000_000.0]
    facts = Facts(store, clock=lambda: clock[0])
    assert facts.save("Ravi", "relation", "brother")[0]
    facts.save("Ravi", "lives in", "Pune")
    facts.save("Ravi", "phone", "98100 11111")
    facts.save("I", "studies at", "IIT Delhi")
    assert facts.save("Ravi", "lives in", "pune")[0] is None              # already known
    assert facts.save("me", "password", "hunter2")[0] is None              # never secrets
    clock[0] += 86400
    fid, said = facts.save("ravi", "phone", "98100 22222")
    assert "was 98100 11111" in said
    lines = facts.relevant("what should I gift my brother for his birthday?")
    assert any("Ravi is your brother" in l for l in lines) and any("Pune" in l for l in lines)
    assert any("98100 22222" in l and "before" in l and "98100 11111" in l for l in lines)
    assert any("IIT Delhi" in l for l in lines)                         # the owner's own facts always come
    facts.forget(fid)                                                   # undo: the old number is current again
    assert [f.value for f in facts.about("Ravi", past=False) if f.attribute == "phone"] == ["98100 11111"]
    facts.save("Ravi", "likes", "cricket")
    facts.save("Ravi", "likes", "chess")                                  # several likes at once
    assert len([f for f in facts.about("Ravi", past=False) if f.attribute == "likes"]) == 2


def test_nightly_tidy_and_undo(make_ctx):
    store = make_ctx().store
    said_on = time.mktime((2026, 10, 1, 12, 0, 0, 0, 0, -1))
    now = [said_on]
    facts = Facts(store, clock=lambda: now[0])
    facts.save("me", "exam date", "physics exam on 3 November")
    facts.save("me", "trip", "Goa trip on 20 December")
    store._write("INSERT INTO facts (subject, attribute, value, created, source) VALUES ('ravi', 'lives in', 'Pune', ?, '')",
                 (said_on,))
    store._write("INSERT INTO facts (subject, attribute, value, created, source) VALUES ('Ravi', 'lives in', 'Pune', ?, '')",
                 (said_on + 5,))
    store.add_memory("Likes dark mode")
    store.add_memory("likes dark mode.")
    now[0] = time.mktime((2026, 11, 11, 12, 0, 0, 0, 0, -1))
    done = facts.tidy()
    assert "past" in done and "duplicate" in done and "memory" in done
    current = {(f.subject, f.value) for f in facts.current()}
    assert ("me", "physics exam on 3 November") not in current and ("me", "Goa trip on 20 December") in current
    assert len([f for f in facts.current() if f.attribute == "lives in"]) == 1 and len(store.memories()) == 1
    past = next(c for c in facts.changes() if "Marked as past" in c["text"])
    assert facts.undo_change(past["id"])
    assert ("me", "physics exam on 3 November") in {(f.subject, f.value) for f in facts.current()}


def test_planner_saves_facts_and_says_so(app_client):
    client, services = app_client()
    orch = services.orchestrator
    plan = {"answer": "Nice, noted.", "topic": "family", "title": "My brother", "remember": [],
            "facts": [{"subject": "Ravi", "attribute": "relation", "value": "brother"},
                      {"subject": "Ravi", "attribute": "birthday", "value": "12 March"}], "steps": []}
    chat = services.store.create_chat("t")
    from prometheus.orchestrator.planner import validate_plan

    p = validate_plan(json.dumps(plan), orch.registry)
    orch._save_facts(chat, 99, p.facts)
    out = orch._finish(chat, services.store.create_task(chat, "x"), "done", "Nice, noted.")
    assert "Remembered" not in out.answer                       # notes belong to their own task only
    tid = services.store.create_task(chat, "y")
    orch._save_facts(chat, tid, [{"subject": "Asha", "attribute": "relation", "value": "sister"}])
    out = orch._finish(chat, tid, "done", "Nice.")
    assert "Remembered: Asha is your sister" in out.answer
    h = {"X-CSRF-Token": login(client)}
    page = client.get("/memory")
    assert "Ravi" in page.text and "12 March" in page.text and "Asha" in page.text
    fid = orch.facts.about("Asha")[0].id
    client.post(f"/memory/facts/{fid}/forget", headers=h)
    assert not orch.facts.about("Asha")
