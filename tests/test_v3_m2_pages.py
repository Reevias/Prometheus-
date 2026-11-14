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


# ---- Memory ---------------------------------------------------------------------------------------------------------
def test_memory_page_tabs_and_search(app_client):
    client, services = app_client()
    login(client)
    st = services.store
    st.add_memory("My bike is a blue Trek", source="chat: Bikes")
    services.orchestrator.facts.save("me", "college", "IIT Delhi", source="chat: Intro")
    page = client.get("/v3/memory").text
    for part in ("My bike is a blue Trek", "People and facts", "college", "IIT Delhi", 'href="/v3/memory"'):
        assert part in page, part
    assert "blue Trek" in client.get("/v3/memory?q=bike").text
    for tab in ("files", "answers", "topics", "privacy"):
        assert client.get(f"/v3/memory?tab={tab}").status_code == 200, tab
    assert client.get("/memory", follow_redirects=False).headers["location"] == "/v3/memory"
    assert "What Prometheus remembers about you" in client.get("/memory?view=classic").text


def test_memory_add_edit_delete_and_secrets(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    mid = client.post("/api/v3/memory/add", data={"text": "I wake up at 7"}, headers=h).json()["id"]
    assert client.post("/api/v3/memory/add", data={"text": "my password is hunter22"}, headers=h).status_code == 400
    assert client.post(f"/api/v3/memory/{mid}/edit", data={"text": "I wake up at 6:30"}, headers=h).json()["ok"]
    assert [m.text for m in st.memories()] == ["I wake up at 6:30"]
    assert [h_.text for h_ in st.recall("6:30", kinds=("memory",))]                     # search sees the new text
    assert not st.recall("wake up at 7", kinds=("memory",)) or "6:30" in st.recall("wake", kinds=("memory",))[0].text
    assert client.post(f"/api/v3/memory/{mid}/edit", data={"text": " "}, headers=h).status_code == 400
    assert client.post("/api/v3/memory/999/edit", data={"text": "x"}, headers=h).status_code == 404
    client.post(f"/api/v3/memory/{mid}/delete", headers=h)
    assert st.memories() == []


def test_fact_edit_keeps_the_old_value_as_past(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    facts = services.orchestrator.facts
    fid, _ = facts.save("me", "city", "Pune")
    assert client.post(f"/api/v3/facts/{fid}/edit", data={"text": "Delhi"}, headers=h).json()["ok"]
    now = [f.value for f in facts.about("me", past=False)]
    assert now == ["Delhi"] and "Pune" in [f.value for f in facts.about("me")]
    assert client.post(f"/api/v3/facts/{fid}/edit", data={"text": "Goa"}, headers=h).status_code == 404   # past now
    new = [f.id for f in facts.about("me", past=False)][0]
    client.post(f"/api/v3/facts/{new}/forget", headers=h)               # forgetting the new value brings back the old
    assert [f.value for f in facts.about("me", past=False)] == ["Pune"]


def test_pause_and_leave_out_stop_saving_by_itself(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    from prometheus.features import memory_collects
    st = services.store
    cid = st.create_chat("Diary")
    assert memory_collects(services.settings, cid)
    client.post("/api/v3/memory/pause", data={"on": "1"}, headers=h)
    assert services.settings.memory_paused and not memory_collects(services.settings, cid)
    client.post("/api/v3/memory/pause", data={}, headers=h)
    assert memory_collects(services.settings, cid)
    # the chat's searchable parts exist, then go when it's left out, and come back when included again
    st.add_message(cid, "user", "my secret diary entry about the hackathon")
    st.add_message(cid, "assistant", "Noted.")                                    # chats are filed after an answer
    for _ in range(50):
        if st.segments(chat_id=cid):
            break
        time.sleep(0.05)
    assert st.segments(chat_id=cid)
    assert client.post(f"/api/v3/memory/chats/{cid}/leave-out", data={"on": "1"}, headers=h).json()["left_out"]
    assert st.segments(chat_id=cid) == [] and not st.document_keys(f"chat:{cid}:")
    assert not memory_collects(services.settings, cid)
    st.add_message(cid, "user", "another entry")                                  # still nothing saved
    st.add_message(cid, "assistant", "Noted again.")
    time.sleep(0.2)
    assert st.segments(chat_id=cid) == []
    assert "Diary" in client.get("/v3/memory?tab=privacy").text
    client.post(f"/api/v3/memory/chats/{cid}/leave-out", data={}, headers=h)
    assert st.segments(chat_id=cid)
    assert client.post("/api/v3/memory/chats/9999/leave-out", data={"on": "1"}, headers=h).status_code == 404


def test_paused_memory_saves_no_facts_by_itself(app_client):
    client, services = app_client()
    login(client)
    services.settings.memory_paused = True
    cid = services.store.create_chat("x")
    tid = services.store.create_task(cid, "my sister Riya studies law")
    services.orchestrator._save_facts(cid, tid, [{"subject": "Riya", "attribute": "studies", "value": "law"}])
    assert services.orchestrator.facts.subjects() == []
    services.settings.memory_paused = False
    services.orchestrator._save_facts(cid, tid, [{"subject": "Riya", "attribute": "studies", "value": "law"}])
    assert [s.lower() for s in services.orchestrator.facts.subjects()] == ["riya"]


def test_memory_export_and_wipe(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    st.add_memory("I like chess")
    services.orchestrator.facts.save("me", "game", "chess")
    r = client.get("/api/v3/memory/export")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    data = r.json()
    assert data["memories"][0]["text"] == "I like chess" and data["facts"][0]["value"] == "chess"
    assert client.post("/api/v3/memory/wipe", data={"confirm": "yes"}, headers=h).status_code == 400
    assert st.memories()
    assert client.post("/api/v3/memory/wipe", data={"confirm": "forget everything"}, headers=h).json()["ok"]
    assert st.memories() == [] and services.orchestrator.facts.about("me") == []


def test_files_tab_searches_inside_files_and_lists_what_it_made(app_client, tmp_path):
    client, services = app_client()
    login(client)
    st = services.store
    st.add_document("file:notes.txt", "notes.txt", str(tmp_path / "notes.txt"), "Photosynthesis makes glucose from light.",
                    private=True)
    cid = st.create_chat("Charts")
    out = tmp_path / "sales.png"
    out.write_bytes(b"x")
    st._write("INSERT INTO outputs (chat_id, path, kind, is_dir, created) VALUES (?, ?, 'image', 0, ?)",
              (cid, str(out), time.time()))
    page = client.get("/v3/memory?tab=files&q=glucose").text
    assert "Photosynthesis" in page and "notes.txt" in page
    made = client.get("/v3/memory?tab=files&show=made").text
    assert "sales.png" in made and "Charts" in made and "Saved pages and files <" not in made


# ---- System and Activity ------------------------------------------------------------------------------------------
def test_system_overview_shows_only_real_states(app_client):
    client, services = app_client()
    login(client)
    page = client.get("/v3/system").text
    for part in ("This laptop", "Prometheus itself", "AI models", "Background jobs", "About", "fake", 'href="/v3/system"'):
        assert part in page, part
    from prometheus.web.pages_v3 import plain_action
    assert plain_action("delete_task") == "Deleted task" and plain_action("skill_switch") == "Skill switch"
    # a job whose thread isn't alive is never called Running
    import threading
    alive = {t.name for t in threading.enumerate()}
    if "prometheus-briefing" not in alive:
        assert "Morning briefing</span><small class=\"muted\">Running" not in page.replace("\n", "")


def test_activity_lists_undoes_and_logs(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    from prometheus import undo
    rid = st.add_reminder(st.create_chat("r"), "call mum", time.time() + 3600)
    if rid is not None:
        undo.record(st, "reminder", "a reminder to call mum", {"id": rid})
    undo.record(st, "email", "an email to Ravi", {})
    st.audit("user", "delete_task", {"task": 3})
    page = client.get("/v3/system?tab=activity").text
    assert "An email to ravi" in page and "can't be unsent" in page and "Deleted task" in page
    assert "Deleted task" in client.get("/v3/system?tab=activity&q=delete").text
    assert "Deleted task" not in client.get("/v3/system?tab=activity&q=zzz").text
    log_id = st._rows("SELECT id FROM undo_log WHERE kind = 'email'")[0]["id"]
    r = client.post("/api/v3/activity/undo", data={"source": "log", "id": str(log_id)}, headers=h)
    assert r.status_code == 200 and "can't be unsent" in r.json()["message"]
    if rid is not None:
        rem_id = st._rows("SELECT id FROM undo_log WHERE kind = 'reminder'")[0]["id"]
        assert client.post("/api/v3/activity/undo", data={"source": "log", "id": str(rem_id)}, headers=h).json()["ok"]
        assert client.post("/api/v3/activity/undo", data={"source": "log", "id": str(rem_id)}, headers=h).status_code == 409
    assert client.post("/api/v3/activity/undo", data={"source": "files", "id": "nope"}, headers=h).status_code == 404
    assert client.post("/api/v3/activity/undo", data={"source": "x", "id": "1"}, headers=h).status_code == 400
    assert client.get("/history", follow_redirects=False).headers["location"] == "/v3/system?tab=activity"


# ---- Approvals ------------------------------------------------------------------------------------------------------
def test_approvals_page_approve_reject_and_secret(app_client):
    import json as _json
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    got = []
    services.approvals.register("wifi_password", lambda payload, secret: got.append(secret) or "Connected",
                                needs_secret=True)
    services.approvals.register("demo", lambda payload, secret: got.append("demo") or "Did the demo")
    wifi = services.approvals.request("wifi_password", "Wi-Fi password for Office-5G", {"ssid": "Office-5G"},
                                      secret_label="Wi-Fi password")
    demo = services.approvals.request("demo", "Run the demo", {"path": "C:/Users/me/demo"})
    page = client.get("/v3/approvals").text
    assert "Wi-Fi password for Office-5G" in page and 'type="password"' in page and "C:/Users/me/demo" in page
    assert 'class="avatar-badge"' in page and 'href="/v3/approvals"' in page                 # badge in your menu
    assert client.post(f"/api/v3/approvals/{wifi}", data={"decision": "approve"}, headers=h).status_code == 400
    r = client.post(f"/api/v3/approvals/{wifi}", data={"decision": "approve", "secret": "pass-123456"}, headers=h)
    assert r.status_code == 200 and got == ["pass-123456"] and "pass-123456" not in r.text
    assert "pass-123456" not in _json.dumps(services.store.audit_log())
    assert client.post(f"/api/v3/approvals/{wifi}", data={"decision": "approve"}, headers=h).status_code == 409
    assert client.post(f"/api/v3/approvals/{demo}", data={"decision": "reject"}, headers=h).status_code == 200
    assert "demo" not in got and services.store.approval(demo).status != "pending"
    assert client.post("/api/v3/approvals/999", data={"decision": "approve"}, headers=h).status_code == 404
    assert "Nothing is waiting" in client.get("/v3/approvals").text
    assert client.get("/approvals", follow_redirects=False).headers["location"] == "/v3/approvals"
