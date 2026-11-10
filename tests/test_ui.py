import re

from conftest import login

from prometheus.orchestrator.planner import TaskOutcome


def authed(app_client, **kw):
    client, services = app_client(**kw)
    token = login(client)
    return client, services, {"X-CSRF-Token": token}


def test_pages_render(app_client):
    client, _, _ = authed(app_client)
    for url in ("/", "/approvals", "/memory", "/history", "/settings", "/status"):
        resp = client.get(url)
        assert resp.status_code == 200, url


def test_send_message_runs_task_and_shows_answer(app_client):
    client, services, h = authed(app_client)

    class FakeOrchestrator:
        def run(self, chat_id, text):
            services.store.add_message(chat_id, "user", text)
            services.store.add_message(chat_id, "assistant", "Sorted 3 files.")
            return TaskOutcome(1, "done", "Sorted 3 files.")

    services.orchestrator = FakeOrchestrator()
    services.start_task = lambda chat_id, text: services.orchestrator.run(chat_id, text)
    chat = services.store.create_chat("Main")
    resp = client.post(f"/chats/{chat}/send", data={"text": "sort my Downloads"}, headers=h)
    assert resp.status_code == 200
    page = client.get(f"/chats/{chat}/messages").text
    assert "sort my Downloads" in page and "Sorted 3 files." in page


def test_messages_are_escaped(app_client):
    client, services, _ = authed(app_client)
    chat = services.store.create_chat("c")
    services.store.add_message(chat, "assistant", "<script>alert(1)</script>")
    page = client.get(f"/chats/{chat}/messages").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_chat_notes(app_client):
    client, services, h = authed(app_client)
    chat = services.store.create_chat("c")
    client.post(f"/chats/{chat}/notes", data={"text": "answer in short"}, headers=h)
    assert [n.text for n in services.store.chat_notes(chat)] == ["answer in short"]


def test_approve_runs_handler(app_client):
    client, services, h = authed(app_client)
    ran = []
    services.approvals.register("test_kind", lambda p: ran.append(p) or "done it")
    aid = services.approvals.request("test_kind", "Do the thing", {"x": 1})
    assert "Do the thing" in client.get("/approvals").text
    resp = client.post(f"/approvals/{aid}", data={"decision": "approve"}, headers=h)
    assert resp.status_code == 200 and ran == [{"x": 1}]
    assert "done it" in resp.text


def test_reject_does_not_run(app_client):
    client, services, h = authed(app_client)
    ran = []
    services.approvals.register("test_kind", lambda p: ran.append(p) or "x")
    aid = services.approvals.request("test_kind", "s", {})
    client.post(f"/approvals/{aid}", data={"decision": "reject"}, headers=h)
    assert ran == []


def test_memory_add_delete_wipe(app_client):
    client, services, h = authed(app_client)
    client.post("/memory", data={"text": "I like short answers"}, headers=h)
    [m] = services.store.memories()
    client.post("/memory", data={"text": "second"}, headers=h)
    client.post(f"/memory/{m.id}/delete", headers=h)
    assert [x.text for x in services.store.memories()] == ["second"]
    client.post("/memory/wipe", headers=h)
    assert services.store.memories() == []


def test_undo_from_history(app_client, tmp_home):
    client, services, h = authed(app_client)
    root = tmp_home.home / "Downloads"
    root.mkdir()
    (root / "a.pdf").write_text("x")
    run = services.journal.start(root)
    services.files.move(run, root / "a.pdf", root / "Documents", root)
    services.journal.finish(run)
    assert run in client.get("/history").text
    resp = client.post(f"/history/{run}/undo", headers=h)
    assert resp.status_code == 200
    assert (root / "a.pdf").exists()


def test_kill_button_sets_switch(app_client):
    client, services, h = authed(app_client)
    client.post("/kill", headers=h)
    assert services.killswitch.is_set
    client.post("/kill/reset", headers=h)
    assert not services.killswitch.is_set


def test_settings_hides_keys_and_saves(app_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    client, services, h = authed(app_client)
    page = client.get("/settings").text
    assert "sk-test-123" not in page
    assert services.settings.password_hash not in page
    client.post("/settings", data={"budget_usd": "2.5", "ollama_model": "qwen3.5:2b",
                                   "anthropic_model": "claude-opus-5-5"}, headers=h)
    assert services.settings.budget_usd == 2.5 and services.budget.cap_usd == 2.5
    assert services.settings.allow_lan is False


def test_settings_rejects_bad_budget(app_client):
    client, services, h = authed(app_client)
    resp = client.post("/settings", data={"budget_usd": "-1", "ollama_model": "m", "anthropic_model": "m"},
                       headers=h)
    assert resp.status_code == 400 and services.settings.budget_usd == 5.0


def test_no_external_urls_in_pages(app_client):
    client, services, _ = authed(app_client)
    services.store.create_chat("c")
    for url in ("/", "/approvals", "/memory", "/history", "/settings", "/login"):
        page = client.get(url).text
        assert not re.search(r'(src|href)=["\']https?://', page), url
