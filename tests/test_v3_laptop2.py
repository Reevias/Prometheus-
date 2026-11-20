"""The owner's second laptop check (20 Nov 2026, evening). Each test failed before its fix."""
from __future__ import annotations

import pytest

from conftest import login


# -- "forgets my name" ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["name", "appearance", "quick"])
def test_settings_changes_keep_the_same_settings_so_nothing_is_wiped_later(app_client, change):
    """Bug: saving the name replaced the settings object; the model switch (and others) later saved the OLD object,
    which had no name, so the name disappeared."""
    from prometheus.config import load_settings, save_settings

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    old = services.settings
    client.post("/api/v3/profile", data={"name": "Saiveer"}, headers=h)
    if change == "appearance":
        client.post("/api/v3/appearance", data={"ui_glow": "1.2", "ui_text": "16"}, headers=h)
    if change == "quick":
        client.post("/api/v3/quick-actions", json={"actions": []}, headers=h)
    assert services.settings is old
    save_settings(old)                       # what the model-switch approval does with the copy it holds
    assert load_settings().owner_name == "Saiveer"


def test_the_name_box_is_part_of_the_page_not_a_browser_popup(app_client):
    """prompt() may not show in the desktop window; the name box is the page's own."""
    client, _ = app_client()
    login(client)
    page = client.get("/").text
    assert 'id="name-dialog"' in page and 'name="name"' in page
    js = client.get("/static/v3/app.js").text
    assert 'prompt("What should Prometheus call you?")' not in js


# -- "deleted chats still show" ------------------------------------------------------------------------------------------

def _two(tmp_path):
    from prometheus.memory.store import Store

    return Store(tmp_path / "laptop.db"), Store(tmp_path / "cloud.db")


def test_a_chat_deleted_on_one_side_is_deleted_on_the_other_and_never_comes_back(tmp_path):
    """Bug: the laptop deleted a chat, the next sync with the cloud copy put it back."""
    from prometheus import sync

    laptop, cloud = _two(tmp_path)
    keep = laptop.create_chat("Keep me")
    gone = laptop.create_chat("Delete me")
    laptop.add_message(gone, "user", "old words")
    sync.merge(cloud, sync.export(laptop, 0))
    assert {t for _, t in cloud.chats()} == {"Keep me", "Delete me"}
    laptop.delete_chat(gone)
    sync.merge(laptop, sync.export(cloud, 0))                   # the cloud still had it: it must not come back
    assert {t for _, t in laptop.chats()} == {"Keep me"}
    sync.merge(cloud, sync.export(laptop, 0))                   # and the cloud learns it was deleted
    assert {t for _, t in cloud.chats()} == {"Keep me"}
    assert not cloud._rows("SELECT 1 FROM messages WHERE text = 'old words'")
    assert keep in dict(laptop.chats())


def test_renaming_a_chat_does_not_bring_back_a_copy_under_the_old_name(tmp_path):
    from prometheus import sync

    laptop, cloud = _two(tmp_path)
    chat = laptop.create_chat("New chat")
    sync.merge(cloud, sync.export(laptop, 0))
    laptop.rename_chat(chat, "Physics homework")
    sync.merge(laptop, sync.export(cloud, 0))
    assert [t for _, t in laptop.chats()] == ["Physics homework"]


def test_two_chats_started_at_the_same_moment_stay_two(tmp_path):
    """Windows clocks are coarse: two chats can start in the same millisecond. They are told apart by their id."""
    from prometheus import sync

    laptop, cloud = _two(tmp_path)
    a, b = laptop.create_chat("One"), laptop.create_chat("Two")
    laptop._write("UPDATE chats SET created = 1000.0")
    laptop.delete_chat(b)
    sync.merge(cloud, sync.export(laptop, 0))
    assert [t for _, t in cloud.chats()] == ["One"]


def test_chats_from_before_ids_match_on_both_sides(tmp_path):
    """Older databases get an id made from name and start time, so already-synced chats aren't doubled."""
    from prometheus.memory.store import chat_uid_for

    assert chat_uid_for("Physics", 1000.0001) == chat_uid_for("Physics", 1000.0)
    assert chat_uid_for("Physics", 1000.0) != chat_uid_for("Maths", 1000.0)


def test_an_existing_database_gets_ids_for_its_old_chats(tmp_path):
    import sqlite3

    from prometheus.memory.store import Store, chat_uid_for

    db = tmp_path / "old.db"
    Store(db)                                              # make the tables, then pretend uid never existed
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE chats DROP COLUMN uid")
    con.execute("INSERT INTO chats (title, created) VALUES ('Old chat', 1234.5)")
    con.commit()
    con.close()
    rows = Store(db)._rows("SELECT uid FROM chats")
    assert [r["uid"] for r in rows] == [chat_uid_for("Old chat", 1234.5)]


# -- "what approval?" ----------------------------------------------------------------------------------------------------

def test_the_waiting_label_says_what_is_waiting_and_where(app_client):
    """Bug: 'Waiting for your approval' showed with no way to find which approval."""
    client, services = app_client()
    login(client)
    chat = services.store.create_chat("Charts")
    services.approvals.register("code_run", lambda p: "ran")
    _, aid = services.approvals.ask("code_run", "Run this Python script", {"code": "print(1)"},
                                    ("code:abc", "code:any"), chat_id=chat)
    state = client.get("/api/v3/state").json()["assistant"]
    assert state["state"] == "waiting_approval"
    assert state["href"] == f"/c/{chat}#ask-{aid}" and "run a Python script" in state["label"]
    assert "print(1)" not in str(state)                     # the kind only, never the request's own text
    page = client.get("/").text
    assert '<a id="assistant-state"' in page


# -- "still can't minimize", "can't full screen it or increase screen length" -------------------------------------------

class FakeWindow:
    def __init__(self):
        self.calls, self.fullscreen = [], False

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a, k))


def test_the_window_buttons_work_in_the_window_itself():
    """Bug: the buttons asked the server to move ITS window. Started with Windows, the server is the background copy,
    which has no window, so nothing happened. The buttons now go straight to the window (pywebview's js_api)."""
    from prometheus.desktop import WindowApi

    api = WindowApi()
    api._window = FakeWindow()
    for action in ("minimize", "fullscreen", "maximize"):
        assert api.window_action(action) is True
    names = [c[0] for c in api._window.calls]
    assert names[:2] == ["minimize", "toggle_fullscreen"] and names[2] in ("maximize", "restore")
    with pytest.raises(ValueError):
        api.window_action("format_disk")


@pytest.mark.parametrize("edge, fix", [("e", 3), ("s", 3), ("se", 3), ("w", 5), ("n", 10), ("nw", 12),
                                       ("ne", 10), ("sw", 5)])
def test_dragging_an_edge_resizes_from_that_edge(edge, fix):
    from prometheus.desktop import WindowApi

    api = WindowApi()
    api._window = FakeWindow()
    api.resize_to(1500, 900, edge)
    name, args, _ = api._window.calls[0]
    assert name == "resize" and args[:2] == (1500, 900) and getattr(args[2], "value", args[2]) == fix
    api.resize_to(100, 100, "se")                          # never smaller than the window's minimum
    assert api._window.calls[1][1][:2] == (860, 560)
