"""Tests for M5 Step 5.6: Conversation branching.

1. "Branch from here" on any message in a chat.
2. Makes a new chat with the history up to that message (in memory/store.py); the original is not changed.
3. The new chat shows where it came from, with a link back.
4. Branching a branch works; deleting the original keeps the branch.
5. Tests for each of the above.
"""
from __future__ import annotations

import pytest
from conftest import login
from prometheus.memory.store import Store


def test_branch_chat_copies_history_and_keeps_original_intact(tmp_path):
    st = Store(tmp_path / "memory.db")
    c1 = st.create_chat("Project Discussion")
    m1 = st.add_message(c1, "user", "Let's build a spacecraft.")
    m2 = st.add_message(c1, "assistant", "Great idea! What propulsion system?", model="claude-3-5")
    m3 = st.add_message(c1, "user", "Ion thrusters.")
    m4 = st.add_message(c1, "assistant", "Ion thrusters have high specific impulse.", model="claude-3-5")

    # Branch at message m2 (the first assistant reply)
    c2 = st.branch_chat(c1, m2)
    assert c2 is not None
    assert c2 != c1

    # Check messages in branch chat c2: should have m1 and m2 only
    msgs_c2 = st.messages(c2)
    assert len(msgs_c2) == 2
    assert [m.text for m in msgs_c2] == ["Let's build a spacecraft.", "Great idea! What propulsion system?"]
    assert msgs_c2[0].role == "user"
    assert msgs_c2[1].role == "assistant"
    assert msgs_c2[1].model == "claude-3-5"

    # Original chat c1 remains completely intact
    msgs_c1 = st.messages(c1)
    assert len(msgs_c1) == 4
    assert [m.text for m in msgs_c1] == [
        "Let's build a spacecraft.",
        "Great idea! What propulsion system?",
        "Ion thrusters.",
        "Ion thrusters have high specific impulse."
    ]

    # Verify search index in branch chat works
    hits = st.search_chats("propulsion")
    chat_ids_hit = [h["chat_id"] for h in hits]
    assert c1 in chat_ids_hit
    assert c2 in chat_ids_hit


def test_branch_chat_parent_link_and_title(tmp_path):
    st = Store(tmp_path / "memory.db")
    c1 = st.create_chat("Original Chat")
    m1 = st.add_message(c1, "user", "Hello")

    # Root chat has no parent
    assert st.chat_parent(c1) is None

    # Branch chat
    c2 = st.branch_chat(c1, m1)
    p = st.chat_parent(c2)
    assert p is not None
    assert p["id"] == c1
    assert p["message_id"] == m1
    assert p["title"] == "Original Chat"
    assert p["exists"] is True

    # Check title of branch defaults to '{orig} (branch)'
    assert st.chat_title(c2) == "Original Chat (branch)"

    # Custom title branch
    c3 = st.branch_chat(c1, m1, title="Custom Branch Title")
    assert st.chat_title(c3) == "Custom Branch Title"


def test_branching_a_branch_works(tmp_path):
    st = Store(tmp_path / "memory.db")
    c1 = st.create_chat("Root")
    m1 = st.add_message(c1, "user", "Step 1")
    m2 = st.add_message(c1, "assistant", "Reply 1")

    # Branch 1 from Root at m2
    c2 = st.branch_chat(c1, m2, title="Branch 1")
    m3 = st.add_message(c2, "user", "Step 2 in Branch 1")
    m4 = st.add_message(c2, "assistant", "Reply 2 in Branch 1")

    # Branch 2 from Branch 1 at m3
    c3 = st.branch_chat(c2, m3, title="Branch 2")
    assert c3 != c2 != c1

    msgs_c3 = st.messages(c3)
    assert len(msgs_c3) == 3
    assert [m.text for m in msgs_c3] == ["Step 1", "Reply 1", "Step 2 in Branch 1"]

    # Parent of Branch 2 points to Branch 1
    p3 = st.chat_parent(c3)
    assert p3 is not None
    assert p3["id"] == c2
    assert p3["message_id"] == m3
    assert p3["title"] == "Branch 1"
    assert p3["exists"] is True

    # Parent of Branch 1 points to Root
    p2 = st.chat_parent(c2)
    assert p2 is not None
    assert p2["id"] == c1
    assert p2["message_id"] == m2
    assert p2["title"] == "Root"
    assert p2["exists"] is True


def test_deleting_original_chat_keeps_branch(tmp_path):
    st = Store(tmp_path / "memory.db")
    c1 = st.create_chat("Ephemeral Root")
    m1 = st.add_message(c1, "user", "Crucial information that must not be lost")
    m2 = st.add_message(c1, "assistant", "Saved reply")

    c2 = st.branch_chat(c1, m2, title="Persistent Branch")

    # Delete original root chat
    st.delete_chat(c1)

    # Root chat is gone
    assert c1 not in dict(st.chats())
    assert len(st.messages(c1)) == 0

    # Branch chat is completely intact
    assert c2 in dict(st.chats())
    msgs_c2 = st.messages(c2)
    assert len(msgs_c2) == 2
    assert msgs_c2[0].text == "Crucial information that must not be lost"
    assert msgs_c2[1].text == "Saved reply"

    # Parent info indicates original was deleted but preserves original title
    p = st.chat_parent(c2)
    assert p is not None
    assert p["id"] == c1
    assert p["title"] == "Ephemeral Root"
    assert p["exists"] is False


def test_branch_chat_validation(tmp_path):
    st = Store(tmp_path / "memory.db")
    c1 = st.create_chat("Chat A")
    c2 = st.create_chat("Chat B")
    m1 = st.add_message(c1, "user", "Msg in A")
    m2 = st.add_message(c2, "user", "Msg in B")

    # Nonexistent chat
    assert st.branch_chat(99999, m1) is None

    # Message does not belong to chat A
    assert st.branch_chat(c1, m2) is None

    # Nonexistent message
    assert st.branch_chat(c1, 99999) is None


def test_branch_routes_and_ui(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    st = services.store

    c1 = st.create_chat("Web Chat")
    m1 = st.add_message(c1, "user", "Question from user")
    m2 = st.add_message(c1, "assistant", "Answer from Prometheus")

    # Messages view has "Branch" button for both user and assistant messages
    msg_html = client.get(f"/c/{c1}/messages").text
    assert 'data-act="branch"' in msg_html
    assert 'title="Branch from here"' in msg_html

    # Call API to branch at m1
    res = client.post(f"/api/v3/chats/{c1}/messages/{m1}/branch", headers=h)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "chat_id" in data
    new_cid = data["chat_id"]
    assert data["url"] == f"/c/{new_cid}"

    # Verify new chat messages
    b_msgs = st.messages(new_cid)
    assert len(b_msgs) == 1
    assert b_msgs[0].text == "Question from user"

    # Branch page renders parent link
    page_html = client.get(f"/c/{new_cid}").text
    assert "branch-chip" in page_html
    assert f"/c/{c1}" in page_html
    assert "From: Web Chat" in page_html

    # HTMX endpoint /chats/{chat_id}/branch/{message_id}
    res_htmx = client.post(f"/chats/{c1}/branch/{m2}", headers=h)
    assert res_htmx.status_code in (204, 303)
    if res_htmx.status_code == 204:
        assert res_htmx.headers.get("HX-Redirect", "").startswith("/c/")
    else:
        assert res_htmx.headers.get("location", "").startswith("/c/")

    # Branching nonexistent chat or message returns 404
    assert client.post(f"/api/v3/chats/{c1}/messages/999999/branch", headers=h).status_code == 404
    assert client.post(f"/api/v3/chats/999999/messages/{m1}/branch", headers=h).status_code == 404
