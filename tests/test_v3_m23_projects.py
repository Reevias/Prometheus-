"""M2.3 Projects: folder, chats, tasks, agents, notes, settings; from a folder; trash; export/import; the planner and the
+ menu know a chat's project; research reports saved into a project."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from conftest import login

from prometheus import projects as pj


def _h(client):
    return {"X-CSRF-Token": login(client)}


def test_blank_project_gets_a_documents_folder(app_client):
    client, services = app_client()
    h = _h(client)
    r = client.post("/api/v3/projects", data={"name": "Physics: Lab*2"}, headers=h)
    assert r.status_code == 200, r.text
    pid = int(r.json()["url"].rsplit("/", 1)[1])
    p = pj.get(services.store, pid)
    assert p["name"] == "Physics  Lab 2" or p["name"] == "Physics Lab 2" or ":" not in p["name"]
    folder = Path(p["folder"])
    assert folder.is_dir() and folder.parent == Path(services.home) / "Documents" / "Prometheus Projects"
    again = client.post("/api/v3/projects", data={"name": "Physics: Lab*2"}, headers=h).json()["url"]
    assert Path(pj.get(services.store, int(again.rsplit("/", 1)[1]))["folder"]).name.endswith("(2)")   # never reused
    assert client.post("/api/v3/projects", data={"name": "  "}, headers=h).status_code == 400
    assert 'href="/v3/projects"' in client.get("/").text                         # in the sidebar, after Chats
    page = client.get("/v3/projects").text
    assert "Physics" in page and "0/0 tasks done" in page


def test_project_from_a_folder_must_be_allowed(app_client):
    client, services = app_client()
    h = _h(client)
    mine = Path(services.home) / "Documents" / "Thesis"
    mine.mkdir(parents=True)
    (mine / "chapter1.docx").write_bytes(b"x")
    r = client.post("/api/v3/projects", data={"name": "Thesis", "folder": str(mine)}, headers=h)
    assert r.status_code == 200, r.text
    pid = int(r.json()["url"].rsplit("/", 1)[1])
    assert "chapter1.docx" in client.get(f"/v3/projects/{pid}").text           # Files tab
    assert client.post("/api/v3/projects", data={"name": "Etc", "folder": "/etc"}, headers=h).status_code == 400
    assert client.post("/api/v3/projects", data={"name": "Gone", "folder": str(mine / "nope")}, headers=h).status_code == 400


def test_chats_tasks_agents_notes_and_planner_context(app_client):
    client, services = app_client()
    h = _h(client)
    st = services.store
    pid = int(client.post("/api/v3/projects", data={"name": "Biology"}, headers=h).json()["url"].rsplit("/", 1)[1])
    url = client.post(f"/api/v3/projects/{pid}/chats/new", headers=h).json()["url"]
    cid = int(url.rsplit("/", 1)[1])
    other = st.create_chat("Loose chat")
    client.post(f"/api/v3/projects/{pid}/chats/{other}", data={"on": "1"}, headers=h)
    assert {c for c, _ in pj.chats(st, pid)} == {cid, other}
    tid = st.create_task(cid, "summarise chapter 3")
    sid = st.add_step(tid, "documents", "Read chapter 3", {})
    st.finish_step(sid, "done", "read")
    st.set_task_status(tid, "done", "ok")
    client.post(f"/api/v3/projects/{pid}/notes", data={"text": "Exam is on 2 Dec"}, headers=h)
    client.post(f"/api/v3/projects/{pid}/edit", data={"about": "Answer in short bullet points"}, headers=h)
    assert "summarise chapter 3" in client.get(f"/v3/projects/{pid}?tab=tasks").text
    assert "Documents" in client.get(f"/v3/projects/{pid}?tab=agents").text
    assert "Exam is on 2 Dec" in client.get(f"/v3/projects/{pid}?tab=notes").text
    assert "Biology" in client.get(f"/c/{cid}").text and "project-chip" in client.get(f"/c/{cid}").text
    ctx = services.orchestrator.project_context(cid)
    assert "Biology" in ctx and "Exam is on 2 Dec" in ctx and "short bullet points" in ctx and pj.get(st, pid)["folder"] in ctx
    assert services.orchestrator.project_context(st.create_chat("x")) == ""
    # the planner really sends it
    from prometheus.models.fake import FakeProvider
    seen = []
    fake = FakeProvider(lambda m, s: seen.append(m[-1]["content"]) or '{"steps": [], "answer": "ok"}', name="fake")
    services.router.providers["fake"] = fake
    services.orchestrator.run(cid, "what's next?")
    assert any("Exam is on 2 Dec" in c for c in seen)
    # take a chat out, notes edit/delete
    client.post(f"/api/v3/projects/{pid}/chats/{other}", data={"on": "0"}, headers=h)
    assert {c for c, _ in pj.chats(st, pid)} == {cid}
    nid = pj.notes(st, pid)[0]["id"]
    client.post(f"/api/v3/projects/{pid}/notes/{nid}/edit", data={"text": "Exam moved to 5 Dec"}, headers=h)
    assert pj.notes(st, pid)[0]["text"] == "Exam moved to 5 Dec"
    client.post(f"/api/v3/projects/{pid}/notes/{nid}/delete", headers=h)
    assert pj.notes(st, pid) == []


def test_trash_restore_and_delete_for_good_keep_files_and_chats(app_client):
    client, services = app_client()
    h = _h(client)
    st = services.store
    pid = int(client.post("/api/v3/projects", data={"name": "Old"}, headers=h).json()["url"].rsplit("/", 1)[1])
    folder = Path(pj.get(st, pid)["folder"])
    (folder / "keep.txt").write_text("mine")
    cid = int(client.post(f"/api/v3/projects/{pid}/chats/new", headers=h).json()["url"].rsplit("/", 1)[1])
    assert client.post(f"/api/v3/projects/{pid}/delete", headers=h).status_code == 409      # not in the trash yet
    client.post(f"/api/v3/projects/{pid}/trash", headers=h)
    assert client.get(f"/v3/projects/{pid}").status_code == 404 and "Trash" in client.get("/v3/projects").text
    client.post(f"/api/v3/projects/{pid}/trash", data={"on": "0"}, headers=h)
    assert client.get(f"/v3/projects/{pid}").status_code == 200
    client.post(f"/api/v3/projects/{pid}/trash", headers=h)
    assert client.post(f"/api/v3/projects/{pid}/delete", headers=h).json()["ok"]
    assert pj.get(st, pid) is None and (folder / "keep.txt").read_text() == "mine" and cid in dict(st.chats())
    assert pj.chat_project(st, cid) is None


def test_export_and_import(app_client):
    client, services = app_client()
    h = _h(client)
    st = services.store
    pid = int(client.post("/api/v3/projects", data={"name": "Trip", "about": "budget trip"}, headers=h).json()["url"].rsplit("/", 1)[1])
    pj.add_note(st, pid, "Book trains early")
    cid = int(client.post(f"/api/v3/projects/{pid}/chats/new", headers=h).json()["url"].rsplit("/", 1)[1])
    st.add_message(cid, "user", "cheap hostels in Goa?")
    st.add_message(cid, "assistant", "Try Anjuna.")
    raw = client.get(f"/api/v3/projects/{pid}/export").content
    data = json.loads(zipfile.ZipFile(io.BytesIO(raw)).read("project.json"))
    assert data["name"] == "Trip" and data["notes"] == ["Book trains early"] and data["chats"][0]["messages"][1]["text"] == "Try Anjuna."
    r = client.post("/api/v3/projects/import", files={"file": ("trip.zip", raw, "application/zip")}, headers=h)
    assert r.status_code == 200, r.text
    new = int(r.json()["url"].rsplit("/", 1)[1])
    assert new != pid and pj.notes(st, new)[0]["text"] == "Book trains early" and len(pj.chats(st, new)) == 1
    assert client.post("/api/v3/projects/import", files={"file": ("x.zip", b"not a zip", "application/zip")},
                       headers=h).status_code == 400


def test_research_report_into_a_project_and_project_files_in_the_plus_menu(app_client):
    client, services = app_client()
    h = _h(client)
    st = services.store
    pid = int(client.post("/api/v3/projects", data={"name": "Energy"}, headers=h).json()["url"].rsplit("/", 1)[1])
    cid = st.create_chat("Research")
    tid = st.create_task(cid, "solar in India")
    st.set_task_status(tid, "done", "Solar is growing [1].")
    r = client.post(f"/api/v3/research/{tid}/to-project", data={"project_id": pid}, headers=h)
    assert r.status_code == 200, r.text
    saved = Path(pj.get(st, pid)["folder"]) / "solar in India.md"
    assert saved.read_text(encoding="utf-8").startswith("# solar in India")
    client.post(f"/api/v3/research/{tid}/to-project", data={"project_id": pid}, headers=h)
    assert (Path(pj.get(st, pid)["folder"]) / "solar in India (2).md").exists()           # never overwrites
    pchat = int(client.post(f"/api/v3/projects/{pid}/chats/new", headers=h).json()["url"].rsplit("/", 1)[1])
    files = client.get(f"/api/v3/chats/{pchat}/project-files").json()
    assert files["project"] == "Energy" and any(f["rel"] == "solar in India.md" for f in files["files"])
    assert client.get(f"/api/v3/chats/{st.create_chat('x')}/project-files").json() == {"project": None, "files": []}


def test_project_files_stay_inside_the_project(app_client):
    client, services = app_client()
    h = _h(client)
    pid = int(client.post("/api/v3/projects", data={"name": "Safe"}, headers=h).json()["url"].rsplit("/", 1)[1])
    opened = []
    import prometheus.agents.opener as op
    real = op.open_output
    op.open_output = lambda path, how, policy: opened.append((path, how)) or "Opened."
    try:
        assert client.post(f"/api/v3/projects/{pid}/files/open", data={"rel": "../../../etc/passwd"}, headers=h).status_code == 409
        assert opened == []
        (Path(pj.get(services.store, pid)["folder"]) / "a.txt").write_text("x")
        assert client.post(f"/api/v3/projects/{pid}/files/open", data={"rel": "a.txt"}, headers=h).json()["ok"]
    finally:
        op.open_output = real
    assert opened and opened[0][0].endswith("a.txt")
