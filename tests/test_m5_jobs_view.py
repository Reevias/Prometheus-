"""M5 step 5.5: the multi-agent view. Everything shown is read from the job tracker; buttons appear only when the move
is allowed and are wired to real routes."""
from __future__ import annotations

import threading
import time

import pytest
from conftest import login

from prometheus.jobs.graph import GraphRunner, Slots
from prometheus.jobs.runner import JobRunner
from prometheus.jobs.store import JobStore
from prometheus.web import jobs_view


@pytest.fixture
def store(tmp_path):
    return JobStore(tmp_path / "jobs.db")


def running_job(store, title="Read the PDF", payload=None, started=1000.0, progress="", kind="work"):
    job = store.create_job(kind, title, payload=payload)
    store.set_state(job.id, "running", progress=progress)
    store.update_job(job.id, started=started)
    return store.get_job(job.id)


class FakeRunner:
    def __init__(self, tasks=()):
        self._tasks = set(tasks)

    def get_task(self, job_id):
        return object() if job_id in self._tasks else None


# -- item 2: what each job shows -------------------------------------------------------------------------------
def test_a_row_shows_only_what_the_tracker_holds(store):
    job = running_job(store, "Read the PDF", started=1000.0, progress="Page 3 of 9")
    rows = jobs_view.build(store, FakeRunner(), graphs=None, now=1090.0)["jobs"]
    row = rows[0]
    assert (row["id"], row["title"], row["state"], row["progress"]) == (job.id, "Read the PDF", "running", "Page 3 of 9")
    assert row["started"] == 1000.0 and row["run_seconds"] == 90.0 and row["run_text"] == "1 min 30 s"
    assert row["memory_text"] == ""             # no program of its own: no number is made up


def test_a_job_that_has_not_started_has_no_start_time_or_run_time(store):
    store.create_job("work", "Waiting")
    row = jobs_view.build(store, FakeRunner(), None, now=5000.0)["jobs"][0]
    assert row["state"] == "pending" and row["started"] == 0.0 and row["run_text"] == ""


def test_a_finished_job_run_time_stops_at_its_end(store):
    job = running_job(store, started=1000.0)
    store.set_state(job.id, "failed", error="Boom")
    store.update_job(job.id, finished=1030.0)
    row = jobs_view.build(store, FakeRunner(), None, now=9999.0)["jobs"][0]
    assert row["state"] == "failed" and row["run_seconds"] == 30.0 and row["error"] == "Boom"


def test_the_agent_of_a_task_step_is_read_from_its_title(store):
    parent = store.create_job("task_graph", "Task 4: summarise", payload={"graph": True})
    store.create_job("graph_step", "research: Find solar prices", parent_id=parent.id,
                     payload={"step": "1", "needs": []})
    row = jobs_view.build(store, FakeRunner(), None, agents={"research"})["jobs"][0]
    assert row["steps"][0]["agent"] == "research"


def test_memory_is_the_real_reading_of_the_steps_programs(store, monkeypatch):
    parent = store.create_job("graph", "G", payload={"graph": True})
    a = store.create_job("graph_step", "a", parent_id=parent.id, payload={"step": "a", "needs": []})
    store.set_state(parent.id, "running")
    store.set_state(a.id, "running")
    from prometheus.jobs import limits

    monkeypatch.setattr(limits, "child_status", lambda s, jid: {"state": "running", "seconds": 1.0,
                                                                "memory_bytes": 150 * 1024 * 1024 if jid == a.id else None})
    row = jobs_view.build(store, FakeRunner(), None)["jobs"][0]
    assert row["memory_text"] == "150 MB" and row["steps"][0]["memory_text"] == "150 MB"


def test_always_on_loops_and_noise_are_not_listed(store):
    running_job(store, "Reminders loop", payload={"service": True})
    done = store.create_job("intent", "Intent check")
    store.set_state(done.id, "running")
    store.set_state(done.id, "done")
    assert jobs_view.build(store, FakeRunner(), None)["jobs"] == []


def test_failed_and_cancelled_jobs_stay_visible(store):
    f = running_job(store, "Fails")
    store.set_state(f.id, "failed", error="Out of memory")
    c = running_job(store, "Cancelled")
    store.set_state(c.id, "cancelled", error="Cancelled by you")
    states = {r["title"]: r["state"] for r in jobs_view.build(store, FakeRunner(), None)["jobs"]}
    assert states == {"Fails": "failed", "Cancelled": "cancelled"}


def test_active_jobs_come_before_finished_ones(store):
    f = running_job(store, "Old failure")
    store.set_state(f.id, "failed", error="x")
    running_job(store, "Now")
    assert [r["title"] for r in jobs_view.build(store, FakeRunner(), None)["jobs"]] == ["Now", "Old failure"]


# -- item 3: the step diagram ----------------------------------------------------------------------------------------
def test_a_graph_gives_its_diagram_levels_and_states(store):
    g = store.create_job("graph", "G", payload={"graph": True})
    ids = {}
    for sid, needs, state in (("a", [], "done"), ("b", ["a"], "running"), ("c", ["a"], "pending"),
                              ("d", ["b", "c"], "pending")):
        j = store.create_job("graph_step", f"step {sid}", parent_id=g.id, payload={"step": sid, "needs": needs})
        ids[sid] = j.id
        if state != "pending":
            store.set_state(j.id, "running")
        if state == "done":
            store.set_state(j.id, "done")
    diagram = jobs_view.build(store, FakeRunner(), None)["jobs"][0]["diagram"]
    nodes = {n["id"]: n for n in diagram["nodes"]}
    assert [nodes[k]["level"] for k in "abcd"] == [0, 1, 1, 2]
    assert [nodes[k]["state"] for k in "abcd"] == ["done", "running", "waiting", "waiting"]
    assert sorted(map(tuple, diagram["edges"])) == [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]


@pytest.mark.parametrize("state,shown", [("pending", "waiting"), ("paused", "waiting"), ("running", "running"),
                                         ("done", "done"), ("failed", "failed"), ("cancelled", "cancelled")])
def test_diagram_states_use_five_names(store, state, shown):
    g = store.create_job("graph", "G", payload={"graph": True})
    j = store.create_job("graph_step", "s", parent_id=g.id, payload={"step": "s", "needs": []})
    if state != "pending":
        store.set_state(j.id, "running")
    if state == "paused":
        store.set_state(j.id, "paused")
    elif state in ("done", "failed", "cancelled"):
        store.set_state(j.id, state)
    node = jobs_view.build(store, FakeRunner(), None)["jobs"][0]["diagram"]["nodes"][0]
    assert node["state"] == shown


# -- item 4: buttons only when the move is allowed ----------------------------------------------------------------
def test_buttons_for_a_graph(store):
    g = store.create_job("graph", "G", payload={"graph": True})
    assert jobs_view.controls_for(store.get_job(g.id), FakeRunner()) == ["stop"]                 # pending
    store.set_state(g.id, "running")
    assert jobs_view.controls_for(store.get_job(g.id), FakeRunner()) == ["pause", "stop"]
    store.set_state(g.id, "paused")
    assert jobs_view.controls_for(store.get_job(g.id), FakeRunner()) == ["resume", "stop"]
    store.set_state(g.id, "cancelled")
    assert jobs_view.controls_for(store.get_job(g.id), FakeRunner()) == []


def test_a_graph_that_cannot_carry_on_has_no_resume(store):
    g = store.create_job("graph", "G", payload={"graph": True, "resumable": False})
    store.set_state(g.id, "running")
    store.set_state(g.id, "paused")
    assert jobs_view.controls_for(store.get_job(g.id), FakeRunner()) == ["stop"]


def test_a_plain_thread_job_has_no_button_that_would_lie(store):
    job = running_job(store)
    assert jobs_view.controls_for(job, FakeRunner()) == []                 # Python can't stop a running thread
    assert jobs_view.controls_for(job, FakeRunner(tasks=[job.id])) == ["stop"]     # an async task can be cancelled
    store.set_state(job.id, "paused")
    assert jobs_view.controls_for(store.get_job(job.id), FakeRunner()) == ["stop"]  # nothing runs: closing is true


def test_a_graph_step_has_no_buttons_of_its_own(store):
    g = store.create_job("graph", "G", payload={"graph": True})
    s = store.create_job("graph_step", "s", parent_id=g.id, payload={"step": "s", "needs": []})
    assert jobs_view.controls_for(store.get_job(s.id), FakeRunner()) == []


# -- the routes and the page ---------------------------------------------------------------------------------------------
@pytest.fixture
def live_graph(app_client):
    """A real graph on the app's own tracker with one step that waits until the test lets it go."""
    client, services = app_client()
    csrf = login(client)
    gate = threading.Event()
    services.graphs.slots = Slots(2)
    gid = services.graphs.create("Compare two laptops", [
        {"id": "a", "title": "research: look up laptop A", "func": lambda ctx, i: gate.wait(10) and None, "retries": 0},
        {"id": "b", "title": "research: look up laptop B", "func": lambda ctx, i: gate.wait(10) and None, "retries": 0},
        {"id": "c", "title": "summarise: compare", "func": lambda ctx, i: "ok", "needs": ["a", "b"], "retries": 0}])
    services.graphs.start(gid)
    deadline = time.time() + 5
    while time.time() < deadline and services.jobs.get_job(gid).state != "running":
        time.sleep(0.02)
    yield client, services, csrf, gid
    gate.set()
    services.graphs.cancel(gid, "test over")


def test_the_agents_page_shows_the_view_instead_of_running_now(live_graph):
    client, services, csrf, gid = live_graph
    page = client.get("/v3/agents").text
    assert "Running now" not in page
    assert 'id="jobs-panel"' in page and "Compare two laptops" in page
    assert 'hx-get="/v3/jobs/panel"' in page and "every" in page.split('hx-get="/v3/jobs/panel"')[1][:120]
    assert f'hx-post="/api/v3/jobs/{gid}/pause"' in page and f'hx-post="/api/v3/jobs/{gid}/stop"' in page
    assert f'hx-post="/api/v3/jobs/{gid}/resume"' not in page          # not allowed while it runs
    assert 'data-graph=' in page and "<canvas" in page


def test_an_empty_tracker_says_so_and_has_no_buttons(app_client):
    client, services = app_client()
    login(client)
    page = client.get("/v3/agents").text
    assert "Nothing is running." in page and "/api/v3/jobs/" not in page


def test_pause_resume_stop_buttons_really_move_the_graph(live_graph):
    client, services, csrf, gid = live_graph
    h = {"X-CSRF-Token": csrf}
    assert client.post(f"/api/v3/jobs/{gid}/pause", headers=h).status_code == 200
    assert services.jobs.get_job(gid).state == "paused"
    assert f'hx-post="/api/v3/jobs/{gid}/resume"' in client.get("/v3/jobs/panel").text
    assert client.post(f"/api/v3/jobs/{gid}/resume", headers=h).status_code == 200
    assert services.jobs.get_job(gid).state in ("pending", "running")
    out = client.post(f"/api/v3/jobs/{gid}/stop", headers=h)
    assert out.status_code == 200 and services.jobs.get_job(gid).state == "cancelled"
    assert all(j.state in ("cancelled", "done") for j in services.jobs.list_jobs(parent_id=gid))
    assert "cancelled" in client.get("/v3/jobs/panel").text.lower()


def test_a_move_that_is_not_allowed_is_refused_and_changes_nothing(live_graph):
    client, services, csrf, gid = live_graph
    h = {"X-CSRF-Token": csrf}
    assert client.post(f"/api/v3/jobs/{gid}/resume", headers=h).status_code == 409      # running, not paused
    step = services.jobs.list_jobs(parent_id=gid)[0]
    assert client.post(f"/api/v3/jobs/{step.id}/stop", headers=h).status_code == 409     # steps follow their graph
    assert client.post("/api/v3/jobs/999999/stop", headers=h).status_code == 404
    assert client.post(f"/api/v3/jobs/{gid}/explode", headers=h).status_code == 404
    assert services.jobs.get_job(gid).state == "running"


def test_the_job_buttons_need_the_csrf_token_and_a_login(live_graph, app_client):
    client, services, csrf, gid = live_graph
    assert client.post(f"/api/v3/jobs/{gid}/stop").status_code in (400, 403)
    assert services.jobs.get_job(gid).state == "running"
    other, _ = app_client()
    assert other.post("/api/v3/jobs/1/stop").status_code in (303, 400, 401, 403)


def test_a_failed_job_shows_its_reason_and_no_buttons(app_client):
    client, services = app_client()
    login(client)
    job = services.jobs.create_job("work", "Convert the video")
    services.jobs.set_state(job.id, "running")
    services.jobs.set_state(job.id, "failed", error="ffmpeg is not installed")
    page = client.get("/v3/jobs/panel").text
    assert "Convert the video" in page and "ffmpeg is not installed" in page and "failed" in page
    assert f"/api/v3/jobs/{job.id}/" not in page


def test_the_stop_action_is_written_in_the_audit_log(live_graph):
    client, services, csrf, gid = live_graph
    client.post(f"/api/v3/jobs/{gid}/pause", headers={"X-CSRF-Token": csrf})
    assert any(a["action"] == "job_pause" and a["detail"]["job"] == gid for a in services.store.audit_log(20))


def test_the_page_has_a_phone_layout():
    from pathlib import Path

    css = (Path(jobs_view.__file__).parent / "static" / "v3" / "theme.css").read_text()
    phone = css[css.index("@media (max-width: 700px)"):]
    assert ".job-row" in phone and ".jobs-panel" in css


def test_a_graph_runner_says_whether_a_paused_graph_can_carry_on(store):
    runner = GraphRunner(store, JobRunner(store), slots=Slots(2))
    gate = threading.Event()
    gid = runner.create("G", [{"id": "a", "func": lambda ctx, i: gate.wait(5) and None, "retries": 0}])
    assert runner.can_resume(gid) is True                       # its functions are in memory in this run
    other = GraphRunner(store, JobRunner(store), slots=Slots(2))
    assert other.can_resume(gid) is False                       # a new run doesn't have them
    gate.set()
    runner.cancel(gid)
