"""M6 audit (27 Nov): findings in 6.1/6.2 from the independent second look. Each test failed before its fix."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from prometheus import sandbox
from prometheus.jobs.states import DONE, FAILED, PAUSED, PENDING, RUNNING
from prometheus.jobs.store import JobStore
from prometheus.runtime import JobSpec, RuntimeRefused, setup_local
from prometheus.runtime import cloudroom as cr
from prometheus.runtime import cloudroom_protocol as proto
from prometheus.runtime.cloudroom_server import create_app

from test_v3_m6_cloudroom import TOKEN, Faulty, move, yes

ESCAPE = ("import _posixsubprocess, os\n"
          "r, w = os.pipe()\n"
          "a = [[b'/bin/sh'], [b'/bin/sh'], True, (), None, None, -1, -1, -1, w, -1, -1, r, w, True, False, -1, None,"
          " None, None, -1, None]\n"
          "try:\n"
          "    try:\n"
          "        pid = _posixsubprocess.fork_exec(*a)\n"
          "    except TypeError:\n"
          "        pid = _posixsubprocess.fork_exec(*a, False)        # Python 3.13 takes one more\n"
          "    print('STARTED', pid)\n"
          "except Exception as e:\n"
          "    print('REFUSED', type(e).__name__)\n")


def _until(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _child(code: str) -> str:
    return subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).stdout


# -- A1 (high): no new process can start from a sandboxed task, by any route ---------------------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="Windows: the sandbox's Job Object allows one process already")
def test_the_sandbox_limits_forbid_new_processes_and_follow_the_time_limit():
    out = _child("from prometheus.sandbox import _posix_limits\nimport resource\n_posix_limits(64, 300, processes=0)()\n"
                 "print(resource.getrlimit(resource.RLIMIT_NPROC), resource.getrlimit(resource.RLIMIT_CPU))")
    assert out.strip() == "(0, 0) (305, 305)", out                # A6 too: CPU time follows the task's own limit


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0,
                    reason="the kernel's process limit binds every user but root (GitHub's Linux runners aren't root)")
def test_a_task_cannot_start_a_shell_through_posixsubprocess(tmp_path):
    res = sandbox.run_locked(ESCAPE, sandbox.new_folder(tmp_path), timeout=20, no_new_processes=True)
    assert "REFUSED" in res.output and "STARTED" not in res.output and "ESCAPED" not in res.output


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Cloudroom runs on Render's Linux machine")
def test_the_cloudroom_server_process_is_not_dumpable():
    out = _child("from prometheus.runtime.cloudroom_server import harden_process\nimport ctypes\nharden_process()\n"
                 "print(ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))")           # PR_GET_DUMPABLE
    assert out.strip() == "0", out


# -- the laptop side --------------------------------------------------------------------------------------------------
@pytest.fixture
def env(tmp_path):
    runs = []

    def runner(task):
        runs.append(task["request_id"])
        code = task["code"]
        if "SLEEP" in code:
            time.sleep(float(code.split("SLEEP")[1].split()[0]))
        return {"ok": True, "output": "ran", "files": {}}

    app = create_app(TOKEN, tmp_path / "cloud", runner=runner)
    store = JobStore(tmp_path / "jobs.db")
    runtimes = setup_local(store)
    http = Faulty(app)
    cloud = cr.CloudroomRuntime(store, http, token=lambda: TOKEN, poll=0.01, waits=(0.01, 0.02, 0.03))
    runtimes.register(cloud)
    return runtimes, cloud, http, runs, store, tmp_path


class Answers(Faulty):
    """Gives back made-up answers for some requests (a proxy page, a 401)."""

    def __init__(self, app):
        super().__init__(app)
        self.fake: list[httpx.Response] = []

    def request(self, method, url, headers=None, content=None):
        if self.fake and "/events" in url:
            return self.fake.pop(0)
        return super().request(method, url, headers=headers, content=content)


# A2: a proxy error page (no boot id) or a 5xx doesn't mean "Cloudroom restarted"
def test_a_proxy_error_while_following_is_retried_not_called_a_restart(env):
    runtimes, cloud, http, runs, store, tmp = env
    ans = Answers(http.client.app)
    cloud.http = ans
    ans.fake = [httpx.Response(502, text="<html>Bad gateway</html>"), httpx.Response(503, json={"error": "busy"})]
    job = runtimes.start(move(code="SLEEP 0.2 x", approval=yes(cloud, move(code="SLEEP 0.2 x"))))
    assert _until(lambda: cloud.status(job.id).state in (DONE, FAILED))
    assert cloud.status(job.id).state == DONE, cloud.status(job.id).error
    assert len(runs) == 1


def test_a_refused_signature_while_following_says_check_the_key_or_clock(env):
    runtimes, cloud, http, runs, store, tmp = env
    ans = Answers(http.client.app)
    cloud.http = ans
    ans.fake = [httpx.Response(401, json={"error": "not signed by the owner's Prometheus"})] * 50
    job = runtimes.start(move(code="SLEEP 0.2 x", approval=yes(cloud, move(code="SLEEP 0.2 x"))))
    assert _until(lambda: cloud.status(job.id).state == FAILED)
    err = cloud.status(job.id).error.lower()
    assert "restarted" not in err and "key" in err and "clock" in err


# A3: a move cut off by a laptop restart (before or right after the receipt) is paused, and Resume carries on
def test_a_move_cut_off_before_its_receipt_is_paused_after_a_restart_and_resumes(env):
    runtimes, cloud, http, runs, store, tmp = env
    http.fail_next = 10_000                                     # the receipt never arrives
    job = runtimes.start(move(approval=yes(cloud, move())))
    time.sleep(0.05)
    cloud.stop_watching(job.id)                                 # Prometheus closes here
    assert store.get_job(job.id).state == PENDING
    store2 = JobStore(tmp / "jobs.db")
    store2.recover_after_restart()
    assert store2.get_job(job.id).state == PAUSED
    http.fail_next = 0
    cloud2 = cr.CloudroomRuntime(store2, http, token=lambda: TOKEN, poll=0.01, waits=(0.01,))
    assert cloud2.resume(job.id) is True
    assert _until(lambda: store2.get_job(job.id).state == DONE)
    assert len(runs) == 1


def test_the_receipt_and_running_are_recorded_together():
    import inspect

    src = inspect.getsource(cr.CloudroomRuntime._submit)
    assert "merge_payload(job_id, receipt=receipt" in src and "set_state(job_id, RUNNING" in src


# A4: a watcher that hits something unexpected marks the task failed instead of leaving it "running"
def test_a_watcher_that_breaks_marks_the_task_failed(env):
    runtimes, cloud, http, runs, store, tmp = env
    key = [TOKEN]
    cloud._token = lambda: key[0]
    job = runtimes.start(move(code="SLEEP 0.5 x", approval=yes(cloud, move(code="SLEEP 0.5 x"))))
    assert _until(lambda: cloud.status(job.id).state == RUNNING)
    key[0] = ""                                                 # the key was removed mid-task
    assert _until(lambda: cloud.status(job.id).state == FAILED)
    assert "key" in cloud.status(job.id).error.lower()


# A8: Pause then Resume never leaves two watchers on one task
def test_only_one_watcher_follows_a_task(env):
    runtimes, cloud, http, runs, store, tmp = env
    job = runtimes.start(move(code="SLEEP 0.6 x", approval=yes(cloud, move(code="SLEEP 0.6 x"))))
    assert _until(lambda: cloud.status(job.id).state == RUNNING)
    store.set_state(job.id, PAUSED, error="Paused by you")
    cloud.resume(job.id)                                         # at once, within one poll
    time.sleep(0.1)
    live = [t for t in threading.enumerate() if t.name == f"cloudroom-{job.id}" and t.is_alive()]
    assert len(live) == 1, live
    assert _until(lambda: cloud.status(job.id).state == DONE)


# A9: what goes up is checked against the yes at the moment it is read for sending
def test_a_file_changed_between_the_check_and_the_send_is_refused(env, monkeypatch):
    runtimes, cloud, http, runs, store, tmp = env
    f = tmp / "a.txt"
    f.write_bytes(b"first")
    spec = move(files={"a.txt": str(f)})
    approval = yes(cloud, spec)
    real = cloud.check_yes

    def check_then_change(a, s):
        ok = real(a, s)
        f.write_bytes(b"changed in between")
        return ok
    monkeypatch.setattr(cloud, "check_yes", check_then_change)
    with pytest.raises(RuntimeRefused, match="changed"):
        runtimes.start(move(files={"a.txt": str(f)}, approval=approval))
    assert runs == [] and http.sent == []


# A10: the owner sees all of the code that goes up
def test_the_question_shows_all_the_code(env):
    runtimes, cloud, http, runs, store, tmp = env
    code = "x = 1\n" * 300 + "print('the hidden tail')\n"
    assert "the hidden tail" in cloud.describe(move(code=code))


# A11: a move that fails after the yes doesn't keep the yes
def test_a_failed_move_forgets_its_yes(env, make_ctx):
    runtimes, cloud, http, runs, store, tmp = env
    ctx = make_ctx()
    cloud.connect_approvals(ctx.approvals, runtimes)
    f = tmp / "a.txt"
    f.write_bytes(b"first")
    approval_id = cloud.request_move(ctx.approvals, move(files={"a.txt": str(f)}))
    f.write_bytes(b"changed")
    ctx.approvals.resolve(approval_id, True)
    assert cloud._yes == {} and runs == []


# A5: the 512 MB server keeps its memory in bounds
def test_an_oversized_unsigned_body_is_refused_before_it_is_read(tmp_path):
    app = create_app(TOKEN, tmp_path / "cloud", runner=lambda t: {"ok": True})
    c = TestClient(app, base_url="https://cloudroom.example")
    r = c.post("/v1/tasks", content=b"x" * (9 * 1024 * 1024), headers={"Content-Length": str(9 * 1024 * 1024)})
    assert r.status_code == 413


def test_finished_tasks_let_go_of_their_files_and_old_ones_are_dropped(tmp_path):
    import prometheus.runtime.cloudroom_server as srv

    app = create_app(TOKEN, tmp_path / "cloud", runner=lambda t: {"ok": True, "output": "", "files": {}})
    c = TestClient(app, base_url="https://cloudroom.example")
    ids = []
    for i in range(srv.KEEP_TASKS + 5):
        body = json.dumps({"request_id": f"r{i}", "kind": "python", "code": "print(1)",
                           "files": {"a.txt": "aGVsbG8="}}).encode()
        ids.append(c.post("/v1/tasks", content=body, headers=proto.sign_headers(TOKEN, "POST", "/v1/tasks", body)
                          ).json()["task_id"])
    time.sleep(0.5)
    state = app.state.cloudroom
    assert len(state["tasks"]) <= srv.KEEP_TASKS
    assert all(not t["files"] for t in state["tasks"].values() if t["state"] in ("done", "failed"))


def test_cloudroom_runs_its_tasks_with_no_new_processes(tmp_path, monkeypatch):
    import prometheus.runtime.cloudroom_server as srv

    seen = {}
    monkeypatch.setattr(sandbox, "run_locked", lambda *a, **k: seen.update(k) or sandbox.RunResult(True, "", []))
    srv.sandbox_runner(tmp_path / "cloud", [])({"code": "print(1)", "files": {}, "seconds": 30, "memory_mb": 64})
    assert seen.get("no_new_processes") is True


def test_result_files_are_size_checked_before_they_are_read(tmp_path, monkeypatch):
    import prometheus.runtime.cloudroom_server as srv

    run = srv.sandbox_runner(tmp_path / "cloud", [])
    res = run({"code": "open('big.bin','wb').write(b'x' * (3 * 1024 * 1024))\nopen('small.txt','w').write('hi')",
               "files": {}, "seconds": 30, "memory_mb": 128})
    assert "small.txt" in res["files"] and "big.bin" not in res["files"]
    assert list((tmp_path / "cloud").iterdir()) == []            # the run folder is gone afterwards


# A12: Cloudroom is wired into the app when it is set up (an address and a key)
def test_the_app_registers_cloudroom_when_it_is_set_up(app_client, monkeypatch):
    from prometheus import config

    monkeypatch.setattr(config, "get_secret", lambda name: TOKEN if name == "CLOUDROOM_TOKEN" else "")
    from prometheus.app import connect_cloudroom

    _, services = app_client()
    services.settings.cloudroom_url = "https://example-cloudroom.onrender.com"
    assert connect_cloudroom(services) is True
    assert services.runtime.get("cloudroom") is not None
    services.settings.cloudroom_url = ""
    assert connect_cloudroom(services) is False


def test_a_paused_cloudroom_task_has_resume_in_the_jobs_view(env):
    from types import SimpleNamespace

    from prometheus.web import jobs_view

    job = SimpleNamespace(state=PAUSED, parent_id=None, kind="cloudroom", payload={"where": "cloudroom"})
    assert jobs_view.controls_for(job, None, None) == ["resume", "stop"]


def test_resume_and_stop_of_a_cloudroom_task_go_to_cloudroom(app_client):
    from conftest import login

    client, services = app_client()
    csrf = login(client)
    calls = []

    class FakeCloud:
        name = "cloudroom"

        def resume(self, job_id):
            calls.append(("resume", job_id))
            return True

        def cancel(self, job_id, reason=""):
            calls.append(("stop", job_id))
            return True

    services.runtime.register(FakeCloud())
    job = services.jobs.create_job("cloudroom", "Count words", payload={"where": "cloudroom"}, state=PAUSED)
    assert client.post(f"/api/v3/jobs/{job.id}/resume", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert client.post(f"/api/v3/jobs/{job.id}/stop", headers={"X-CSRF-Token": csrf}).status_code == 200
    assert calls == [("resume", job.id), ("stop", job.id)]
