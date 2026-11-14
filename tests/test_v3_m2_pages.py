"""M2.2: the new-look pages on what Prometheus already does. Tasks first."""
from __future__ import annotations

import time

from conftest import login

from prometheus.web.pages_v3 import since_for, took


def _task(st, text="sort my downloads", status="done", answer="Done: 12 files sorted.", chat=None):
    cid = chat or st.create_chat("Downloads")
    tid = st.create_task(cid, text)
    sid = st.add_step(tid, "file_sorter", "Sort Downloads", {"folder": "Downloads"})
    st.finish_step(sid, "done", "Moved 12 files")
    st.add_agent_note(tid, "file_sorter", "Left 2 files it couldn't classify")
    st.set_task_status(tid, status, answer)
    return cid, tid


def test_helpers():
    assert took(100, 104) == "4 s" and took(100, 225) == "2 min 5 s" and took(0, 5) == "" and took(10, 5) == ""
    now = time.time()
    assert since_for("all") == 0 and abs(since_for("week", now) - (now - 7 * 86400)) < 1
    assert 0 <= now - since_for("today", now) <= 86400


def test_tasks_page_lists_filters_and_searches(app_client):
    client, services = app_client()
    login(client)
    st = services.store
    _task(st)
    _task(st, "summarise the physics chapter", status="failed", answer="The file was missing.")
    page = client.get("/v3/tasks").text
    assert "sort my downloads" in page and "summarise the physics chapter" in page and "Failed" in page
    assert 'href="/v3/tasks"' in page                                          # the sidebar's Tasks goes here now
    done = client.get("/v3/tasks?status=done").text
    assert "sort my downloads" in done and "physics" not in done
    found = client.get("/v3/tasks?q=missing").text                             # searches answers too
    assert "physics" in found and "sort my downloads" not in found
    assert "No task matches" in client.get("/v3/tasks?q=nothing-like-this").text
    part = client.get("/v3/tasks?status=failed", headers={"HX-Request": "true", "HX-Target": "task-list"}).text
    assert "physics" in part and "<aside" not in part                       # just the list, for live filtering


def test_task_detail_shows_the_timeline_notes_and_answer(app_client):
    client, services = app_client()
    login(client)
    _, tid = _task(services.store)
    page = client.get(f"/v3/tasks/{tid}").text
    for part in ("You asked", "File Sorter", "Sort Downloads", "Moved 12 files", "What it used", "Downloads",
                 "Left 2 files it couldn", "Done: 12 files sorted.", "Run again", "Save record"):
        assert part in page, part
    assert client.get("/v3/tasks/99999").status_code == 404


def test_task_actions(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    st = services.store
    cid, tid = _task(st)
    # stop: only a running task (or a paused one, which then won't resume)
    assert client.post(f"/api/v3/tasks/{tid}/stop", headers=h).status_code == 409
    from prometheus import resume
    _, paused = _task(st, "translate this", status="paused", answer="Paused: the AI limit.")
    resume.note_limit(st, st.task(paused)["chat_id"], "translate this", "rate limit reached")
    r = client.post(f"/api/v3/tasks/{paused}/stop", headers=h)
    assert r.status_code == 200 and st.task(paused)["status"] == "stopped"
    assert not any(i["chat_id"] == st.task(paused)["chat_id"] for i in resume._queue(st))
    # running: the stop reaches the orchestrator
    services.running[cid] = True
    st.set_task_status(tid, "running")
    assert client.post(f"/api/v3/tasks/{tid}/stop", headers=h).status_code == 200
    assert cid in services.orchestrator.stopping
    assert client.post(f"/api/v3/tasks/{tid}/retry", headers=h).status_code == 409      # still answering
    assert client.post(f"/api/v3/tasks/{tid}/delete", headers=h).status_code == 409     # stop it first
    services.running.pop(cid)
    services.orchestrator.stopping.discard(cid)
    st.set_task_status(tid, "done", "ok")
    # retry: the same request again, in its chat
    r = client.post(f"/api/v3/tasks/{tid}/retry", headers=h)
    assert r.status_code == 200 and r.json()["url"] == f"/c/{cid}"
    for _ in range(100):
        if cid not in services.running and len(st.list_tasks(q="sort my downloads")) == 2:
            break
        time.sleep(0.05)
    assert len(st.list_tasks(q="sort my downloads")) == 2
    # save record, then delete (the chat stays)
    rec = client.get(f"/api/v3/tasks/{tid}/export")
    assert rec.status_code == 200 and rec.json()["steps"][0]["result"] == "Moved 12 files"
    assert "attachment" in rec.headers["content-disposition"]
    assert client.post(f"/api/v3/tasks/{tid}/delete", headers=h).json() == {"deleted": tid}
    assert st.task(tid) == {} and st.steps(tid) == [] and cid in dict(st.chats())
    assert client.post(f"/api/v3/tasks/{tid}/delete", headers=h).status_code == 404
    # a deleted chat can't be retried into
    st.delete_chat(cid)
    _, orphan = _task(st, chat=cid)
    assert client.post(f"/api/v3/tasks/{orphan}/retry", headers=h).status_code == 409


def test_actions_need_the_csrf_token(app_client):
    client, services = app_client()
    login(client)
    _, tid = _task(services.store)
    assert client.post(f"/api/v3/tasks/{tid}/delete").status_code in (403, 400)
    assert services.store.task(tid)
