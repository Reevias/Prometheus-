"""M2.3 task runner (second check, from the other session's tests): Pause at a safe point keeps only the steps not
run; Carry on runs exactly those, also after a restart; Stop while paused; the board and timeline views."""
from __future__ import annotations

import json
import threading
import time

from conftest import login

from prometheus.agents.base import Agent, StepResult

PLAN = {"steps": [{"id": i, "agent": "ticker", "goal": f"tick {i}", "skill": "", "inputs": {"n": i}, "depends_on": []}
                  for i in (1, 2, 3)], "answer": ""}


class Ticker(Agent):
    name = "ticker"
    description = "Counts (test agent)."
    tools: set = set()
    ran: list = []
    gate = threading.Event()

    def run(self, goal, inputs, ctx):
        Ticker.ran.append(inputs.get("n"))
        if inputs.get("n") == 1:
            Ticker.gate.wait(5)                    # step 1 stays running until the test lets it finish
        return StepResult("done", f"ticked {inputs.get('n')}", {"n": inputs.get("n")})


def _services(app_client):
    from prometheus.models.fake import FakeProvider

    def reply(messages, system):
        text = messages[-1]["content"]
        return "All three ticked." if text.startswith("Request:") else json.dumps(PLAN)

    client, services = app_client(providers=[FakeProvider(reply, name="fake")])
    services.orchestrator.registry["ticker"] = Ticker
    Ticker.ran, Ticker.gate = [], threading.Event()
    return client, services


def _wait(cond, secs=5.0):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_pause_waits_after_the_running_step_and_resume_carries_on(app_client):
    client, services = _services(app_client)
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    cid = st.create_chat("Ticks")
    services.start_task(cid, "tick three times")
    assert _wait(lambda: Ticker.ran == [1])
    tid = st.latest_task(cid)["id"]
    r = client.post(f"/api/v3/tasks/{tid}/pause", headers=h)
    assert r.status_code == 200, r.text
    Ticker.gate.set()                                            # step 1 finishes; step 2 must not start
    assert _wait(lambda: st.task(tid)["status"] == "paused")
    assert _wait(lambda: cid not in services.running)
    time.sleep(0.3)
    assert Ticker.ran == [1] and str(tid) in (st.kv_get("paused_runs") or {})
    detail = client.get(f"/v3/tasks/{tid}").text
    assert "Paused" in detail and f"/api/v3/tasks/{tid}/resume" in detail.replace('data-task="resume" data-id="', "/api/v3/tasks/").replace('"', "/resume", 0) or "resume" in detail
    assert client.post(f"/api/v3/tasks/{tid}/resume", headers=h).status_code == 200
    assert _wait(lambda: st.task(tid)["status"] == "done")
    assert Ticker.ran == [1, 2, 3] and st.task(tid)["answer"] == "All three ticked."
    assert str(tid) not in (st.kv_get("paused_runs") or {})


def test_stop_while_paused_ends_it(app_client):
    client, services = _services(app_client)
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    cid = st.create_chat("Ticks")
    services.start_task(cid, "tick three times")
    assert _wait(lambda: Ticker.ran == [1])
    tid = st.latest_task(cid)["id"]
    client.post(f"/api/v3/tasks/{tid}/pause", headers=h)
    Ticker.gate.set()
    assert _wait(lambda: st.task(tid)["status"] == "paused")
    assert _wait(lambda: cid not in services.running)
    client.post(f"/api/v3/tasks/{tid}/stop", headers=h)
    assert _wait(lambda: st.task(tid)["status"] == "stopped")
    assert _wait(lambda: cid not in services.running) and Ticker.ran == [1]
    assert client.post(f"/api/v3/tasks/{tid}/resume", headers=h).status_code == 409        # stopped can't carry on


def test_resume_after_a_restart_uses_the_checkpoint(app_client):
    """A task held when Prometheus closed: Resume runs only the steps that hadn't run, with the earlier results."""
    client, services = _services(app_client)
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    cid = st.create_chat("Ticks")
    tid = st.create_task(cid, "tick three times")
    st.set_task_status(tid, "paused")
    st.kv_set("paused_runs", {str(tid): {
        "text": "tick three times", "steps": PLAN["steps"][1:], "only": "", "read_only": False, "topic": "",
        "bad": [], "private": [], "done": {"1": {"status": "done", "summary": "ticked 1", "data": {"n": 1}}}}})
    Ticker.gate.set()
    r = client.post(f"/api/v3/tasks/{tid}/resume", headers=h)
    assert r.status_code == 200, r.text
    assert _wait(lambda: st.task(tid)["status"] == "done")
    assert Ticker.ran == [2, 3]                                   # step 1 was not run again
    assert client.post(f"/api/v3/tasks/{tid}/resume", headers=h).status_code == 409


def test_pause_refused_when_not_running(app_client):
    client, services = _services(app_client)
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    cid = st.create_chat("x")
    tid = st.create_task(cid, "x")
    st.set_task_status(tid, "done", "ok")
    assert client.post(f"/api/v3/tasks/{tid}/pause", headers=h).status_code == 409
    assert client.post(f"/api/v3/tasks/{tid}/resume", headers=h).status_code == 409


def test_board_and_timeline_views(app_client):
    client, services = _services(app_client)
    login(client)
    st = services.store
    cid = st.create_chat("Views")
    done = st.create_task(cid, "sort downloads")
    sid = st.add_step(done, "file_sorter", "Sort Downloads", {})
    st.finish_step(sid, "done", "moved 3")
    st.set_task_status(done, "done", "ok")
    failed = st.create_task(cid, "read a missing file")
    st.set_task_status(failed, "failed", "not found")
    held = st.create_task(cid, "long job")
    st.set_task_status(held, "paused")
    st.kv_set("paused_runs", {str(held): {"text": "long job", "steps": [], "done": {}, "bad": [], "private": []}})
    board = client.get("/v3/tasks?view=board").text
    for part in ("Running", "Paused", "Done", "Failed", "Stopped",
                 "sort downloads", "read a missing file", f"/api/v3/tasks/{held}/resume", f"/api/v3/tasks/{done}/retry"):
        assert part in board, part
    assert "draggable" not in board                                            # buttons, not dragging
    tl = client.get("/v3/tasks?view=timeline").text
    assert "sort downloads" in tl and "read a missing file" in tl
    part = client.get("/v3/tasks?view=board", headers={"HX-Request": "true", "HX-Target": "task-list"}).text
    assert "board-col" in part and "<aside" not in part
