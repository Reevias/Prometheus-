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


# ---- Skills ---------------------------------------------------------------------------------------------------------
def _user_skill(services, name="my-notes", body="# Notes\n\nHow I take notes.", refs=None):
    folder = services.skills.user / name
    (folder / "references").mkdir(parents=True, exist_ok=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Use when the owner asks how they like their "
                                     f"notes written.\nversion: 1.2\nauthor: Me\n---\n{body}", encoding="utf-8")
    for ref, text in (refs or {}).items():
        (folder / "references" / ref).write_text(text, encoding="utf-8")
    return folder


def test_skills_page_groups_searches_and_shows_details(app_client):
    client, services = app_client()
    login(client)
    _user_skill(services, refs={"style.md": "Short bullet points."})
    page = client.get("/v3/skills").text
    for part in ("Code", "Research and learning", "Yours", "My notes", "Built in", "Added by you", 'href="/v3/skills"'):
        assert part in page, part
    assert "My notes" in client.get("/v3/skills?q=notes").text and "Coding" not in client.get("/v3/skills?q=notes").text
    detail = client.get("/v3/skills/my-notes").text
    for part in ("How I take notes.", "1.2", "Me", "style.md", "Not used yet", "Check it", "Try it", "Remove"):
        assert part in detail, part
    assert "Short bullet points." in client.get("/v3/skills/my-notes?ref=style.md").text
    assert 'id="skill-remove"' not in client.get("/v3/skills/research").text   # built-in: switch off, never delete
    assert client.get("/v3/skills/nope").status_code == 404
    assert client.get("/skills", follow_redirects=False).headers["location"] == "/v3/skills"
    assert client.get("/skills?view=classic").status_code == 200


def test_skill_switch_hides_it_from_the_planner(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    assert "research" in services.skills.catalog_text()
    r = client.post("/api/v3/skills/research/switch", data={}, headers=h)
    assert r.json() == {"skill": "research", "on": False}
    assert "research" not in services.skills.catalog_text() and services.skills.get("research") is None
    assert services.skills.get_any("research") is not None and "research" in services.settings.skills_off
    assert client.post("/api/v3/skills/research/try", data={"question": "x"}, headers=h).status_code == 409
    client.post("/api/v3/skills/research/switch", data={"on": "1"}, headers=h)
    assert services.skills.get("research") is not None
    assert client.post("/api/v3/skills/nope/switch", headers=h).status_code == 404


def test_skill_check_finds_problems(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/v3/skills/research/check", headers=h).json()["ok"] is True
    folder = _user_skill(services, "big-one", body="x" * 25_000)
    (folder / "references" / "script.py").write_text("print(1)")
    res = client.post("/api/v3/skills/big-one/check", headers=h).json()
    texts = " ".join(r["text"] for r in res["results"])
    assert res["ok"] is False and "longer than" in texts and "script.py" in texts


def test_skill_try_and_remove(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    folder = _user_skill(services)
    assert client.post("/api/v3/skills/my-notes/try", data={"question": " "}, headers=h).status_code == 400
    r = client.post("/api/v3/skills/my-notes/try", data={"question": "write today's notes"}, headers=h)
    assert r.status_code == 200
    cid = r.json()["chat_id"]
    for _ in range(100):
        if cid not in services.running and services.store.messages(cid):
            break
        time.sleep(0.05)
    assert services.store.messages(cid)[0].text == "Use the my-notes skill: write today's notes"
    assert client.post("/api/v3/skills/research/remove", headers=h).status_code == 409    # built in
    assert client.post("/api/v3/skills/my-notes/remove", headers=h).json() == {"removed": "my-notes"}
    assert not folder.exists() and services.skills.get_any("my-notes") is None


def test_skill_use_is_recorded_and_listed(app_client):
    client, services = app_client()
    login(client)
    st = services.store
    cid = st.create_chat("x")
    tid = st.create_task(cid, "research solar panels")
    sid = st.add_step(tid, "research", "Find sources", {})
    st.finish_step(sid, "done", "Found 5", {"skill": "research"})
    st.set_task_status(tid, "done", "ok")
    page = client.get("/v3/skills/research").text
    assert "Find sources" in page and f"/v3/tasks/{tid}" in page
    assert "used 1 time<" in client.get("/v3/skills").text.replace("\n", "")


def test_add_skill_from_a_github_link(app_client, monkeypatch):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    from prometheus import repo_of_day

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d = code, data

        def json(self):
            return self._d

    class Http:
        def get(self, url, headers=None):
            if url.endswith("/readme"):
                import base64
                return Resp(200, {"content": base64.b64encode(b"# Tool\nIt sorts photos.").decode(), "encoding": "base64"})
            if "missing/repo" in url:
                return Resp(404, {})
            return Resp(200, {"full_name": "someone/photo-sorter", "description": "Sorts photos by date",
                              "language": "Python", "stargazers_count": 10})

    real = repo_of_day.RepoOfTheDay.__init__
    monkeypatch.setattr(repo_of_day.RepoOfTheDay, "__init__", lambda self, services, http=None: real(self, services, Http()))
    assert client.post("/api/v3/skills/add", data={"link": "not a link"}, headers=h).status_code == 400
    assert client.post("/api/v3/skills/add", data={"link": "github.com/missing/repo"}, headers=h).status_code == 404
    r = client.post("/api/v3/skills/add", data={"link": "https://github.com/someone/photo-sorter.git"}, headers=h)
    assert r.status_code == 200, r.text
    assert services.skills.get_any("repo-someone-photo-sorter") is not None
    assert "From GitHub" in client.get("/v3/skills").text
