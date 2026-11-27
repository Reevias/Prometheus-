"""M6: the items the audit left open (27 Nov). Each test failed before its fix."""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from prometheus import sandbox
from prometheus.jobs.states import DONE
from prometheus.jobs.store import JobStore
from prometheus.runtime import cloudroom as cr
from prometheus.runtime import cloudroom_protocol as proto
from prometheus.runtime.cloudroom_server import create_app

from test_v3_m6_cloudroom import TOKEN, Faulty, move, yes

ROOT = Path(__file__).resolve().parent.parent


def _until(cond, seconds=15.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# -- 1a: a place in Settings for the Render address and the Cloudroom key ------------------------------------------
def test_settings_save_the_cloudroom_address_and_key_and_connect_it(app_client, monkeypatch):
    from conftest import login

    from prometheus import config

    saved = {}
    monkeypatch.setattr(config, "set_secret", lambda name, value: saved.__setitem__(name, value))
    monkeypatch.setattr(config, "get_secret", lambda name: saved.get(name, ""))
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    page = client.get("/settings").text
    assert 'id="cloudroom"' in page
    r = client.post("/settings/cloudroom", data={"url": "https://my-room.onrender.com/", "key": "k" * 40,
                                                 "password": "wrong"}, headers=h)
    assert r.status_code == 403 and services.settings.cloudroom_url == ""
    r = client.post("/settings/cloudroom", data={"url": "http://not-https.example", "key": "",
                                                 "password": "correct horse battery"}, headers=h)
    assert r.status_code == 400 and services.settings.cloudroom_url == ""
    r = client.post("/settings/cloudroom", data={"url": "https://my-room.onrender.com/", "key": "k" * 40,
                                                 "password": "correct horse battery"}, headers=h)
    assert r.status_code == 200
    assert services.settings.cloudroom_url == "https://my-room.onrender.com" and saved["CLOUDROOM_TOKEN"] == "k" * 40
    assert services.runtime.get("cloudroom") is not None


def test_the_cloudroom_section_is_an_advanced_setting():
    text = (ROOT / "prometheus/web/templates/v3/settings.html").read_text(encoding="utf-8")
    assert '("cloudroom", "Cloudroom (Render)", True)' in text


# -- 1b: the code agent can send a script to Cloudroom when the owner asks; the answer comes back to the chat -------
class FakeCloud:
    name = "cloudroom"

    def __init__(self):
        self.asked = []

    def request_move(self, approvals, spec):
        self.asked.append(spec)
        return 7

    def describe(self, spec):
        return "ok"


def test_the_code_agent_asks_to_move_a_script_to_cloudroom(make_ctx, monkeypatch):
    from prometheus.agents import code_agent

    ctx = make_ctx()
    cloud = FakeCloud()
    monkeypatch.setattr(code_agent, "_cloudroom", lambda: cloud)
    res = code_agent.CodeAgent().run("sum", {"code": "print(sum(range(10)))", "cloudroom": True}, ctx)
    assert res.status == "waiting_approval" and "Approvals" in res.summary
    spec = cloud.asked[0]
    assert spec.runtime == "cloudroom" and "print(sum(range(10)))" in spec.payload["cloud"]["code"]
    assert spec.payload["chat_id"] == ctx.chat_id()


def test_without_cloudroom_set_up_the_agent_says_so(make_ctx, monkeypatch):
    from prometheus.agents import code_agent

    monkeypatch.setattr(code_agent, "_cloudroom", lambda: None)
    res = code_agent.CodeAgent().run("sum", {"code": "print(sum(range(3)))", "cloudroom": True}, make_ctx())
    assert res.status == "failed" and "Cloudroom isn't set up" in res.summary


def test_a_script_that_does_not_fit_stays_with_the_reason(make_ctx, monkeypatch):
    from prometheus.agents import code_agent
    from prometheus.runtime import RuntimeRefused

    cloud = FakeCloud()
    cloud.request_move = lambda a, s: (_ for _ in ()).throw(RuntimeRefused("Tasks in Cloudroom have no internet"))
    monkeypatch.setattr(code_agent, "_cloudroom", lambda: cloud)
    res = code_agent.CodeAgent().run("x", {"code": "import urllib.request", "cloudroom": True}, make_ctx())
    assert res.status == "failed" and "no internet" in res.summary


def test_a_finished_cloudroom_task_tells_its_chat(tmp_path):
    from prometheus.memory.store import Store
    from prometheus.runtime import setup_local

    mem = Store(tmp_path / "memory.db")
    chat = mem.create_chat("c")
    store = JobStore(mem)
    runtimes = setup_local(store)
    app = create_app(TOKEN, tmp_path / "cloud", runner=lambda t: {"ok": True, "output": "45\n", "files": {}})
    cloud = cr.CloudroomRuntime(store, Faulty(app), token=lambda: TOKEN, poll=0.01, waits=(0.01,))
    cloud.on_finished = cr.tell_chat(mem)
    runtimes.register(cloud)
    spec = move(code="print(45)")
    spec.payload["chat_id"] = chat
    m = move(code="print(45)", approval=yes(cloud, spec))
    m.payload["chat_id"] = chat
    job = runtimes.start(m)
    assert _until(lambda: cloud.status(job.id).state == DONE)
    assert _until(lambda: any("45" in msg.text and "Cloudroom" in msg.text for msg in mem.messages(chat)))


# -- 2: Stop really stops a task that is running on Render --------------------------------------------------------
def test_run_locked_stops_when_asked(tmp_path):
    flag = threading.Event()
    threading.Timer(0.5, flag.set).start()
    t = time.monotonic()
    res = sandbox.run_locked("import time\ntime.sleep(30)\n", sandbox.new_folder(tmp_path), timeout=60,
                             should_stop=flag.is_set)
    assert time.monotonic() - t < 10 and res.ok is False and "stopped" in res.output.lower()


def test_cancel_on_the_server_ends_the_running_program(tmp_path):
    app = create_app(TOKEN, tmp_path / "cloud")
    c = TestClient(app, base_url="https://cloudroom.example")

    def call(method, path, data=None):
        body = json.dumps(data).encode() if data is not None else b""
        return c.request(method, path, content=body or None, headers=proto.sign_headers(TOKEN, method, path, body))

    slow = call("POST", "/v1/tasks", {"request_id": "slow", "kind": "python", "code": "import time\ntime.sleep(60)",
                                      "seconds": 120}).json()["task_id"]
    assert _until(lambda: any(e["state"] == "running" for e in call("GET", f"/v1/tasks/{slow}/events").json()["events"]))
    t = time.monotonic()
    call("POST", f"/v1/tasks/{slow}/cancel")
    quick = call("POST", "/v1/tasks", {"request_id": "quick", "kind": "python", "code": "print('hi')"}).json()["task_id"]
    assert _until(lambda: call("GET", f"/v1/tasks/{quick}/result").json().get("finished"), 30)
    assert time.monotonic() - t < 15                          # the next task didn't wait a minute behind it


# -- 3: the key goes to Render as a secret file, not an environment variable ---------------------------------------
def test_render_takes_the_key_as_a_secret_file():
    y = (ROOT / "cloud/render/render.yaml").read_text(encoding="utf-8")
    guide = (ROOT / "docs/cloud-setup.md").read_text(encoding="utf-8")
    assert "CLOUDROOM_TOKEN" not in y.split("envVars", 1)[-1] if "envVars" in y else True
    assert "Secret File" in guide and "/etc/secrets/CLOUDROOM_TOKEN" in guide


# -- 4: on Linux no task can start another program, by any route, and threads still work --------------------------
ESCAPE_TO_FILE = ("import _posixsubprocess, os, time\n"
                  "r, w = os.pipe()\n"
                  "a = [[b'/bin/sh', b'-c', b'echo x > ESCAPED'], [b'/bin/sh'], True, (), None, None, -1, -1, -1, -1,"
                  " -1, -1, r, w, True, False, -1, None, None, None, -1, None]\n"
                  "try:\n"
                  "    try:\n"
                  "        pid = _posixsubprocess.fork_exec(*a)\n"
                  "    except TypeError:\n"
                  "        pid = _posixsubprocess.fork_exec(*a, False)\n"
                  "    os.waitpid(pid, 0)\n"
                  "except Exception as e:\n"
                  "    print('REFUSED', type(e).__name__)\n"
                  "print('ESCAPED' if os.path.exists('ESCAPED') else 'NO-SHELL')\n")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the kernel filter is Linux's; Windows has a Job Object")
def test_on_linux_a_task_cannot_run_another_program_even_as_root(tmp_path):
    res = sandbox.run_locked(ESCAPE_TO_FILE, sandbox.new_folder(tmp_path), timeout=20)
    assert "NO-SHELL" in res.output and "ESCAPED\n" not in res.output.replace("NO-SHELL", ""), res.output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_threads_still_work_in_the_linux_sandbox(tmp_path):
    code = "import threading\nout=[]\nt=threading.Thread(target=lambda: out.append(42))\nt.start(); t.join()\nprint(out)"
    res = sandbox.run_locked(code, sandbox.new_folder(tmp_path), timeout=20)
    assert "[42]" in res.output, res.output


# -- the deadlock found on GitHub's Windows run of main (27 Nov): the programs lock never waits for the database ------
def test_writing_down_programs_never_waits_for_the_database_while_holding_the_programs_lock(tmp_path):
    from prometheus.jobs import limits

    store = JobStore(tmp_path / "jobs.db")
    job = store.create_job("graph_step", "s")
    token = 987654
    limits._PROGRAMS[token] = {"job_id": job.id, "pid": 1, "pgid": None, "win_job": None, "created": None,
                               "store": store, "attempt": 0}
    try:
        with store._lock:                       # a graph driver holds the database lock and then needs the programs lock
            t = threading.Thread(target=limits.untrack, args=(token,), daemon=True)
            t.start()
            time.sleep(0.2)
            got = limits._PROGRAMS_LOCK.acquire(timeout=1)
            assert got, "untrack holds the programs lock while waiting for the database: the two can deadlock"
            limits._PROGRAMS_LOCK.release()
        t.join(5)
    finally:
        limits._PROGRAMS.pop(token, None)


def test_the_graph_driver_pauses_outside_the_database_lock():
    import inspect

    from prometheus.jobs.graph import GraphRunner

    src = inspect.getsource(GraphRunner._drive)
    locked = [l for l in src.split("with self.store._lock:", 1)[1].split("\n")]
    inside = []
    for line in locked[1:]:
        if line.strip() and (len(line) - len(line.lstrip())) <= 12:      # back at the `with` level: the lock is let go
            break
        inside.append(line)
    assert inside and not any("self.pause(" in l for l in inside)
