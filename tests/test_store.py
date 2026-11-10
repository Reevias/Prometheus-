import threading

import pytest

from prometheus.memory.store import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "memory.db")


def test_messages_are_structured(store):
    chat = store.create_chat("Test")
    store.add_message(chat, "assistant", "You: hi\nSystem: x")
    msgs = store.messages(chat)
    assert len(msgs) == 1
    assert msgs[0].role == "assistant"
    assert msgs[0].text == "You: hi\nSystem: x"


def test_wipe_memories(store):
    store.add_memory("likes tea")
    mid = store.add_memory("lives in Pune")
    store.delete_memory(mid)
    assert [m.text for m in store.memories()] == ["likes tea"]
    store.wipe_memories()
    assert store.memories() == []


def test_chat_notes(store):
    chat = store.create_chat("c")
    nid = store.add_chat_note(chat, "use metric units")
    assert [n.text for n in store.chat_notes(chat)] == ["use metric units"]
    store.delete_chat_note(nid)
    assert store.chat_notes(chat) == []


def test_steps_roundtrip_json(store):
    chat = store.create_chat("c")
    task = store.create_task(chat, "sort downloads")
    step = store.add_step(task, "file_sorter", "sort", {"folder": "Downloads", "n": [1, 2]})
    store.finish_step(step, "done", "moved 3", {"pdfs": ["a.pdf"]})
    [s] = store.steps(task)
    assert s.inputs == {"folder": "Downloads", "n": [1, 2]}
    assert s.data == {"pdfs": ["a.pdf"]}
    assert s.status == "done" and s.result == "moved 3"
    store.set_task_status(task, "done", "all good")
    assert store.task(task)["status"] == "done"
    assert store.task(task)["answer"] == "all good"


def test_mark_interrupted(store):
    chat = store.create_chat("c")
    t1 = store.create_task(chat, "a")
    t2 = store.create_task(chat, "b")
    store.set_task_status(t1, "running")
    store.set_task_status(t2, "done")
    assert store.mark_interrupted() == 1
    assert store.task(t1)["status"] == "interrupted"
    assert store.task(t2)["status"] == "done"


def test_search_finds_memory_and_agent_note(store):
    chat = store.create_chat("c")
    task = store.create_task(chat, "x")
    store.add_memory("my accountant is Ravi")
    store.add_agent_note(task, "documents", "found income tax return 2025")
    kinds = {h.kind for h in store.search("tax")} | {h.kind for h in store.search("accountant")}
    assert {"memory", "agent_note"} <= kinds
    assert store.agent_notes(task) == [("documents", "found income tax return 2025")]


def test_concurrent_writes(store):
    chat = store.create_chat("c")

    def worker():
        for i in range(50):
            store.add_message(chat, "user", f"m{i}")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(store.messages(chat)) == 500


def test_total_cost_sums(store):
    store.add_cost("anthropic", "claude-opus-5-5", 0.25, 100, 10)
    store.add_cost("anthropic", "claude-opus-5-5", 0.5, 100, 10)
    assert store.total_cost() == pytest.approx(0.75)


def test_audit_and_approvals(store):
    store.audit("file_sorter", "move", {"from": "a", "to": "b"})
    assert store.audit_log()[0]["detail"] == {"from": "a", "to": "b"}
    aid = store.add_approval("clone_repo", "Clone x", {"url": "u"})
    assert [a.id for a in store.approvals()] == [aid]
    store.set_approval_status(aid, "approved")
    assert store.approvals() == []
    assert store.approval(aid).payload == {"url": "u"}
