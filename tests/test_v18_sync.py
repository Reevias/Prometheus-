import time

import pytest

from prometheus import sync
from prometheus.memory.store import Store


def fill(store, tag):
    chat = store.create_chat(f"{tag} chat")
    store.add_message(chat, "user", f"hello from {tag}")
    store.add_memory(f"{tag} likes chai")
    store.add_reminder(chat, f"{tag} reminder", time.time() + 3600, "")
    from prometheus.study import Study

    Study(store).add("physics", f"{tag} question?", "answer")
    from prometheus.money import Money

    Money(store).add(100, f"{tag} shop", ref=f"{tag}-1")
    return chat


def test_two_way_merge_without_doubles(tmp_path):
    a, b = Store(tmp_path / "a.db"), Store(tmp_path / "b.db")
    fill(a, "laptop")
    fill(b, "phone")
    got_b = sync.merge(b, sync.export(a, 0))
    got_a = sync.merge(a, sync.export(b, 0))
    assert got_b["messages"] == 1 and got_a["memories"] == 1 and got_a["cards"] == 1 and got_a["spending"] == 1
    for s in (a, b):
        assert {t for _, t in s.chats()} == {"laptop chat", "phone chat"}
        assert len(s.memories()) == 2 and len(s.reminders()) == 2
    again = sync.merge(a, sync.export(b, 0))                  # a second sync adds nothing
    assert sum(again.values()) == 0


def test_a_reminder_that_rang_rings_nowhere_else(tmp_path):
    a, b = Store(tmp_path / "a.db"), Store(tmp_path / "b.db")
    chat = a.create_chat("c")
    rid = a.add_reminder(chat, "call mom", time.time() - 10, "")
    sync.merge(b, sync.export(a, 0))
    b_r = b._rows("SELECT id FROM reminders")[0]["id"]
    b.reminder_fired(b_r, None)                                  # rang on the cloud copy
    sync.merge(a, sync.export(b, 0))
    assert not a.reminders_due(time.time())


def test_cloud_stays_quiet_while_the_laptop_is_on(app_client):
    _, services = app_client()
    services.settings.role = "cloud"
    services.store.kv_set("laptop_seen", time.time())
    assert sync.quiet(services) and services.reminders.tick() == 0
    services.store.kv_set("laptop_seen", time.time() - 3600)
    assert not sync.quiet(services)
    services.settings.role = "laptop"
    assert not sync.quiet(services)


def test_sync_endpoint_needs_the_key(app_client):
    client, services = app_client()
    assert client.post("/api/sync", json={}).status_code == 401
    key = sync.token()
    services.store.add_memory("cloud memory")
    r = client.post("/api/sync", json={"since": 0, "data": {"memories": [{"text": "laptop memory", "created": 1.0,
                                                                         "source": ""}]}},
                    headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200 and r.json()["got"]["memories"] == 1
    assert any(m["text"] == "cloud memory" for m in r.json()["data"]["memories"])
    assert services.store.kv_get("laptop_seen")
