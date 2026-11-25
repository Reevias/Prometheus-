"""M6 step 6.2: Cloudroom (items 1-5). Every test failed before the code was written."""
from __future__ import annotations

import json
import random
import sys
import threading
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, RUNNING
from prometheus.jobs.store import JobStore
from prometheus.runtime import JobSpec, RuntimeRefused, setup_local
from prometheus.runtime import cloudroom as cr
from prometheus.runtime import cloudroom_protocol as proto
from prometheus.runtime.cloudroom_server import create_app

TOKEN = "t0k3n-for-tests-only-0123456789abcdef"
_IDS = iter(range(1, 10_000))


def yes(cloud, spec) -> str:
    """The owner's yes for exactly this move, recorded the way the Approvals page handler records it."""
    move_id = f"test-yes-{next(_IDS)}"
    cloud._record_yes(move_id, cloud.move_payload(spec))
    return move_id


def _until(cond, seconds=10.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


class Faulty:
    """An HTTP client in front of the Cloudroom server that can lose the link, lose answers, or shuffle events."""

    def __init__(self, app):
        self.client = TestClient(app, base_url="https://cloudroom.example")
        self.fail_next = 0           # this many requests fail before reaching the server
        self.lose_answer = 0         # this many requests reach the server, but the answer is lost
        self.shuffle = False
        self.sent: list[httpx.Request] = []

    def swap(self, app):
        self.client = TestClient(app, base_url="https://cloudroom.example")

    def request(self, method, url, headers=None, content=None):
        if self.fail_next:
            self.fail_next -= 1
            raise httpx.ConnectError("link lost")
        resp = self.client.request(method, url, headers=headers, content=content)
        self.sent.append(resp.request)
        if self.lose_answer:
            self.lose_answer -= 1
            raise httpx.ReadTimeout("answer lost")
        if self.shuffle and url.endswith("/events") or (self.shuffle and "/events?" in url):
            data = resp.json()
            random.shuffle(data["events"])
            data["events"] = data["events"] + data["events"][:1]          # and a duplicate
            return httpx.Response(resp.status_code, json=data, request=resp.request)
        return resp


@pytest.fixture
def server(tmp_path):
    runs = []

    def runner(task):                       # stands in for the sandbox in most tests: fast and countable
        runs.append(task["request_id"])
        code = task["code"]
        if "SLEEP" in code:
            time.sleep(float(code.split("SLEEP")[1].split()[0]))
        return {"ok": True, "output": f"ran {len(code)} chars", "files": {}}

    app = create_app(TOKEN, tmp_path / "cloud", runner=runner)
    return app, runs


@pytest.fixture
def setup(tmp_path, server):
    app, runs = server
    store = JobStore(tmp_path / "jobs.db")
    runtimes = setup_local(store)
    http = Faulty(app)
    cloud = cr.CloudroomRuntime(store, http, token=lambda: TOKEN, poll=0.01, waits=(0.01, 0.02, 0.03))
    runtimes.register(cloud)
    return runtimes, cloud, http, runs, app


def move(title="Count words", code="print(len('hello world'.split()))", approval=None, **kw):
    return JobSpec("cloudroom", title, payload={"cloud": {"kind": "python", "code": code, **kw}}, runtime="cloudroom",
                   approval=approval)


# -- item 1: HTTPS with a signed token; nothing else of the owner's goes up ----------------------------------------
def test_the_server_refuses_unsigned_or_wrongly_signed_requests(server):
    app, runs = server
    c = TestClient(app, base_url="https://cloudroom.example")
    body = json.dumps({"request_id": "r1", "kind": "python", "code": "print(1)"}).encode()
    assert c.post("/v1/tasks", content=body).status_code == 401
    bad = proto.sign_headers("wrong-token-wrong-token-wrong", "POST", "/v1/tasks", body)
    assert c.post("/v1/tasks", content=body, headers=bad).status_code == 401
    ok = proto.sign_headers(TOKEN, "POST", "/v1/tasks", body)
    assert c.post("/v1/tasks", content=body, headers=ok).status_code == 200
    assert c.post("/v1/tasks", content=body, headers=ok).status_code == 401          # the same signature twice
    old = proto.sign_headers(TOKEN, "POST", "/v1/tasks", body, now=time.time() - 3600)
    assert c.post("/v1/tasks", content=body, headers=old).status_code == 401         # too old
    assert c.get("/v1/health").status_code == 200                                    # the pinger needs no key


def test_the_token_is_a_saved_key():
    from prometheus.config import KEY_NAMES

    assert "CLOUDROOM_TOKEN" in KEY_NAMES


def test_only_the_cloudroom_token_signs_and_no_other_key_goes_up(setup, monkeypatch):
    runtimes, cloud, http, runs, app = setup
    monkeypatch.setattr(cr, "_other_secrets", lambda: ["sk-ant-SECRET-KEY-123456", "ghp_GITHUBTOKEN987"])
    approval = yes(cloud, move())
    runtimes.start(move(approval=approval))
    assert _until(lambda: runs)
    for req in http.sent:
        raw = (str(req.headers) + req.content.decode("utf-8", "replace"))
        assert "sk-ant-SECRET" not in raw and "ghp_GITHUB" not in raw and TOKEN not in raw   # signed, never sent


def test_a_task_holding_another_key_is_refused_before_anything_goes_up(setup, monkeypatch):
    runtimes, cloud, http, runs, app = setup
    monkeypatch.setattr(cr, "_other_secrets", lambda: ["sk-ant-SECRET-KEY-123456"])
    spec = move(code="key = 'sk-ant-SECRET-KEY-123456'")
    with pytest.raises(RuntimeRefused, match="key"):
        cloud.describe(spec)
    assert http.sent == [] and runs == []


# -- item 2: moving asks first and shows what goes up ---------------------------------------------------------------
def test_moving_asks_on_the_approvals_page_and_shows_what_goes_up(setup, make_ctx, tmp_path):
    runtimes, cloud, http, runs, app = setup
    ctx = make_ctx()
    data = tmp_path / "numbers.csv"
    data.write_text("1,2,3\n")
    spec = move(code="print(open('numbers.csv').read())", files={"numbers.csv": str(data)})
    approval_id = cloud.request_move(ctx.approvals, spec)
    item = ctx.store.approval(approval_id)
    assert item.kind == cr.APPROVAL_KIND and item.status == "pending"
    for words in ("numbers.csv", "6 bytes", "print(open", "no internet", "seconds", "MB"):
        assert words in item.summary, item.summary
    assert runs == [] and http.sent == []                         # nothing went up before the yes


def test_without_a_yes_or_with_someone_elses_yes_nothing_moves(setup):
    runtimes, cloud, http, runs, app = setup
    with pytest.raises(RuntimeRefused, match="yes"):
        runtimes.start(move())
    other = yes(cloud, move(code="print('other task')"))
    with pytest.raises(RuntimeRefused, match="yes"):
        runtimes.start(move(approval=other))                      # a yes for a different task
    assert http.sent == []


def test_a_yes_works_once_and_a_file_changed_after_it_is_refused(setup, tmp_path):
    runtimes, cloud, http, runs, app = setup
    data = tmp_path / "a.txt"
    data.write_text("first")
    spec = move(files={"a.txt": str(data)})
    approval = yes(cloud, spec)
    data.write_text("changed after the yes")
    with pytest.raises(RuntimeRefused, match="changed"):
        runtimes.start(move(files={"a.txt": str(data)}, approval=approval))
    data.write_text("first")
    runtimes.start(move(files={"a.txt": str(data)}, approval=approval))
    with pytest.raises(RuntimeRefused, match="yes"):
        runtimes.start(move(files={"a.txt": str(data)}, approval=approval))   # used already


def test_the_approvals_page_yes_starts_the_move(setup, make_ctx):
    runtimes, cloud, http, runs, app = setup
    ctx = make_ctx()
    cloud.connect_approvals(ctx.approvals, runtimes)
    approval_id = cloud.request_move(ctx.approvals, move())
    ctx.approvals.resolve(approval_id, True)
    assert _until(lambda: len(runs) == 1)


# -- item 5 (fit check): a task too big for the free machine stays on the laptop ------------------------------------
@pytest.mark.parametrize("extra, words", [({"memory_mb": 400}, "memory"), ({"seconds": 3600}, "time"),
                                          ({"network": True}, "internet")])
def test_a_task_that_does_not_fit_stays_on_the_laptop(setup, extra, words):
    runtimes, cloud, http, runs, app = setup
    with pytest.raises(RuntimeRefused, match=words):
        cloud.describe(move(**extra))


def test_too_many_bytes_stay_on_the_laptop(setup, tmp_path):
    runtimes, cloud, http, runs, app = setup
    big = tmp_path / "big.bin"
    big.write_bytes(b"x" * (cr.MAX_UPLOAD + 1))
    with pytest.raises(RuntimeRefused, match="MB"):
        cloud.describe(move(files={"big.bin": str(big)}))


# -- item 3: request ids, receipts, events in order, never run twice -----------------------------------------------
def test_a_move_gets_a_signed_receipt_runs_once_and_brings_back_the_result(setup):
    runtimes, cloud, http, runs, app = setup
    job = runtimes.start(move(approval=yes(cloud, move())))
    assert _until(lambda: cloud.status(job.id).state == DONE)
    rec = cloud.status(job.id).payload
    assert proto.verify_receipt(TOKEN, rec["receipt"]) and rec["receipt"]["request_id"] == rec["request_id"]
    assert runs == [rec["request_id"]]
    assert cloud.result(job.id)["output"].startswith("ran ")
    states = [e["state"] for e in cloud.events(job.id)]
    assert states[0] == "pending" and states[-1] == DONE


def test_the_same_request_twice_is_not_run_twice(server):
    app, runs = server
    c = TestClient(app, base_url="https://cloudroom.example")
    body = json.dumps({"request_id": "same-id", "kind": "python", "code": "print(1)"}).encode()
    r1 = c.post("/v1/tasks", content=body, headers=proto.sign_headers(TOKEN, "POST", "/v1/tasks", body)).json()
    r2 = c.post("/v1/tasks", content=body, headers=proto.sign_headers(TOKEN, "POST", "/v1/tasks", body)).json()
    assert r1 == r2
    assert _until(lambda: runs == ["same-id"])
    time.sleep(0.2)
    assert runs == ["same-id"]


def test_events_out_of_order_or_twice_are_put_right(setup):
    runtimes, cloud, http, runs, app = setup
    http.shuffle = True
    job = runtimes.start(move(approval=yes(cloud, move())))
    assert _until(lambda: cloud.status(job.id).state == DONE)
    remote = cloud.status(job.id).payload["remote_seen"]
    assert remote == list(range(1, len(remote) + 1))              # every server event once, in order


# -- item 4: a lost link retries, resumes from the receipt, or fails cleanly; never twice ----------------------------
def test_a_lost_link_while_waiting_is_retried_and_the_task_finishes_once(setup):
    runtimes, cloud, http, runs, app = setup
    job = runtimes.start(move(code="SLEEP 0.3 x", approval=yes(cloud, move(code="SLEEP 0.3 x"))))
    assert _until(lambda: runs)
    http.fail_next = 2
    assert _until(lambda: cloud.status(job.id).state == DONE)
    assert len(runs) == 1


def test_an_answer_lost_after_the_server_took_the_task_is_not_run_twice(setup):
    runtimes, cloud, http, runs, app = setup
    http.lose_answer = 1                                          # the server got it; the receipt never arrived
    job = runtimes.start(move(approval=yes(cloud, move())))
    assert _until(lambda: cloud.status(job.id).state == DONE)
    assert len(runs) == 1


def test_a_link_that_stays_down_marks_the_task_failed(setup):
    runtimes, cloud, http, runs, app = setup
    http.fail_next = 10_000
    job = runtimes.start(move(approval=yes(cloud, move())))
    assert _until(lambda: cloud.status(job.id).state == FAILED)
    assert "couldn't reach" in cloud.status(job.id).error.lower()
    assert runs == []


def test_a_cloudroom_restart_after_the_receipt_fails_the_task_and_never_resends_it(setup, tmp_path):
    runtimes, cloud, http, runs, app = setup
    job = runtimes.start(move(code="SLEEP 5 x", approval=yes(cloud, move(code="SLEEP 5 x"))))
    assert _until(lambda: cloud.status(job.id).payload.get("receipt"))
    fresh_runs = []
    http.swap(create_app(TOKEN, tmp_path / "cloud2", runner=lambda t: fresh_runs.append(t) or {"ok": True}))
    assert _until(lambda: cloud.status(job.id).state == FAILED)
    assert "restarted" in cloud.status(job.id).error.lower()
    assert fresh_runs == []                                       # never sent a second time


def test_after_a_laptop_restart_resume_follows_the_receipt_without_resending(setup, tmp_path):
    runtimes, cloud, http, runs, app = setup
    job = runtimes.start(move(code="SLEEP 0.5 x", approval=yes(cloud, move(code="SLEEP 0.5 x"))))
    assert _until(lambda: cloud.status(job.id).payload.get("receipt"))
    cloud.stop_watching(job.id)                                   # the laptop's process ends here
    store2 = JobStore(tmp_path / "jobs.db")
    store2.recover_after_restart()
    assert store2.get_job(job.id).state == PAUSED
    cloud2 = cr.CloudroomRuntime(store2, http, token=lambda: TOKEN, poll=0.01, waits=(0.01,))
    assert cloud2.resume(job.id) is True
    assert _until(lambda: store2.get_job(job.id).state == DONE)
    assert len(runs) == 1


def test_cancel_reaches_the_cloud(setup):
    runtimes, cloud, http, runs, app = setup
    job = runtimes.start(move(code="SLEEP 5 x", approval=yes(cloud, move(code="SLEEP 5 x"))))
    assert _until(lambda: cloud.status(job.id).payload.get("receipt"))
    assert cloud.cancel(job.id) is True
    assert cloud.status(job.id).state == CANCELLED
    assert any(r.url.path.endswith("/cancel") for r in http.sent)


# -- item 5: the server's sandbox ----------------------------------------------------------------------------------
@pytest.mark.skipif(sys.platform == "win32", reason="Cloudroom runs on Render's Linux machine")
def test_a_task_in_the_cloud_sandbox_cannot_read_the_token_and_has_limits(tmp_path, monkeypatch):
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    (secret_dir / "CLOUDROOM_TOKEN").write_text(TOKEN)
    app = create_app(TOKEN, tmp_path / "cloud", secret_paths=[str(secret_dir)])
    c = TestClient(app, base_url="https://cloudroom.example")

    def submit(rid, code, **extra):
        body = json.dumps({"request_id": rid, "kind": "python", "code": code, **extra}).encode()
        return c.post("/v1/tasks", content=body, headers=proto.sign_headers(TOKEN, "POST", "/v1/tasks", body)).json()

    def wait(task_id):
        path = f"/v1/tasks/{task_id}/result"
        for _ in range(600):
            r = c.get(path, headers=proto.sign_headers(TOKEN, "GET", path, b""))
            if r.status_code == 200 and r.json().get("finished"):
                return r.json()
            time.sleep(0.05)
        raise AssertionError("no result")

    steal = ("import os\nout=[]\n"
             f"for p in [{str(secret_dir / 'CLOUDROOM_TOKEN')!r}, '/proc/%d/environ' % os.getppid()]:\n"
             "    try:\n        out.append(open(p).read()[:40])\n    except Exception as e:\n        out.append('no')\n"
             "print(out, os.environ.get('CLOUDROOM_TOKEN'))\n")
    res = wait(submit("steal", steal)["task_id"])
    assert TOKEN not in res["output"] and "['no', 'no'] None" in res["output"]
    slow = wait(submit("slow", "import time\ntime.sleep(30)", seconds=1)["task_id"])
    assert slow["ok"] is False and slow["timed_out"] is True
