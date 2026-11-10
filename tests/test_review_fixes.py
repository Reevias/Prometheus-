"""Tests for the final-review findings (each failed before its fix)."""
import dataclasses
import json
import socket

import httpx
import pytest

from conftest import login

from prometheus.actions.files import FileActions
from prometheus.actions.journal import Journal
from prometheus.agents.base import Agent, StepResult
from prometheus.agents.documents import Documents
from prometheus.agents.file_sorter import FileSorter
from prometheus.auth import LoginLimiter
from prometheus.memory.store import Store
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator
from prometheus.safety.approvals import Approvals
from prometheus.safety.paths import PathBlocked, PathPolicy
from prometheus.safety.web import fetch_text
from prometheus.selfedit.patcher import Patcher


# F1: the data folder can be read but never changed
def test_data_dir_cannot_be_changed(tmp_path):
    home = tmp_path / "home"
    data = home / "AppData" / "Local" / "Prometheus"
    (data / "journals").mkdir(parents=True)
    policy = PathPolicy(home=home, data_dir=data, code_dir=tmp_path / "code")
    assert policy.check_read(data / "journals" / "x.json")
    with pytest.raises(PathBlocked):
        policy.check_change(data / "journals" / "x.json", task_root=data / "journals")


# F2: private step results never flow into web/cloud agents
class Secretive(Agent):
    name = "documents"
    description = "d"
    tools = set()
    private_outputs = True

    def run(self, goal, inputs, ctx):
        return StepResult("done", "summary of my tax return", {"summaries": ["C:/Users/me/tax.pdf: income 9 lakh"]})


class Web(Agent):
    name = "research"
    description = "r"
    tools = set()
    accepts_private = False
    called = []

    def run(self, goal, inputs, ctx):
        Web.called.append(inputs)
        return StepResult("done", "searched")


def test_private_results_not_sent_to_research(make_ctx):
    Web.called.clear()
    plan = json.dumps({"steps": [
        {"id": 1, "agent": "documents", "goal": "g", "inputs": [], "depends_on": []},
        {"id": 2, "agent": "research", "goal": "g", "inputs": [{"key": "query", "value": "$step1.summaries"}],
         "depends_on": [1]}]})
    p = FakeProvider([plan, "answer"], name="p")
    ctx0 = make_ctx(providers=[p], routes={"plan": ["p"], "combine": ["p"], "private": ["p"]})
    orch = Orchestrator(ctx0.store, ctx0.router, {"documents": Secretive, "research": Web},
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch)
    out = orch.run(ctx0.store.create_chat("c"), "x")
    assert Web.called == []
    steps = ctx0.store.steps(out.task_id)
    assert steps[1].status == "failed" and "private" in steps[1].result.lower()


# F3: the fetch connects to the exact address that was checked (no DNS rebinding)
def test_fetch_pins_checked_address(monkeypatch):
    answers = iter(["93.184.216.34", "127.0.0.1", "127.0.0.1"])

    def fake_dns(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    seen = {}

    def handler(request):
        seen["host"] = request.url.host
        seen["header"] = request.headers["host"]
        seen["sni"] = request.extensions.get("sni_hostname")
        return httpx.Response(200, text="<p>hello world</p>")

    fetch_text("https://rebind.example/page", httpx.Client(transport=httpx.MockTransport(handler)))
    assert seen["host"] == "93.184.216.34"
    assert seen["header"] == "rebind.example" and seen["sni"] == "rebind.example"


# F4: undo can't race a running sort, and a failed undo can be retried
def test_undo_refused_while_run_active_and_retry_allowed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    root = home / "Downloads"
    root.mkdir(parents=True)
    (root / "a.txt").write_text("x")
    journal = Journal(tmp_path / "j")
    files = FileActions(PathPolicy(home=home, data_dir=tmp_path / "d", code_dir=tmp_path / "c"), journal)
    run = journal.start(root)
    files.move(run, root / "a.txt", root / "T", root)
    report = files.undo_run(run)
    assert report.restored == 0 and "still running" in " ".join(report.problems)
    journal.finish(run)
    (root / "a.txt").write_text("blocker")  # something is in the way: undo can't finish
    assert files.undo_run(run).problems
    assert not journal.load(run).get("undone")
    (root / "a.txt").unlink()
    assert files.undo_run(run).restored == 1
    assert journal.load(run)["undone"]


def test_journal_writes_are_serialised(tmp_path):
    import threading

    journal = Journal(tmp_path / "j")
    run = journal.start(tmp_path)
    threads = [threading.Thread(target=lambda i=i: journal.record(run, {"op": "move", "n": i})) for i in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(journal.load(run)["entries"]) == 40


# F5: self-edits only add plugin files; the rest of the code is locked
@pytest.mark.parametrize("rel", ["models/router.py", "agents/base.py", "agents/research.py", "actions/files.py",
                                 "app.py", "config.py", "__init__.py", "memory/store.py"])
def test_self_edit_cannot_touch_core(tmp_path, rel):
    code = tmp_path / "code"
    (code / rel).parent.mkdir(parents=True, exist_ok=True)
    (code / rel).write_text("# ANCHOR\nX = 1\n")
    patcher = Patcher(code, Approvals(Store(tmp_path / "m.db")), tmp_path / "b")
    assert patcher.propose_insert(rel, "# ANCHOR", "Y = 2", "r").status == "refused"


# F6: Windows system files are left alone
def test_sorter_skips_desktop_ini(tmp_home, make_ctx):
    d = tmp_home.home / "Downloads"
    d.mkdir()
    (d / "desktop.ini").write_text("[.ShellClassInfo]")
    (d / "Thumbs.db").write_text("x")
    (d / "a.txt").write_text("x")
    FileSorter().run("s", {"folder": "Downloads"}, make_ctx())
    assert (d / "desktop.ini").exists() and (d / "Thumbs.db").exists()


# F7: unknown Host headers are refused; login attempts are counted before the slow check
def test_untrusted_host_rejected(app_client):
    client, _ = app_client()
    assert client.get("/login", headers={"host": "evil.example"}).status_code == 400


def test_login_attempts_counted_before_verify():
    limiter = LoginLimiter()
    assert all(limiter.attempt("c") for _ in range(5))
    assert limiter.attempt("c") is False
    limiter.reset("c")
    assert limiter.attempt("c") is True


# F8 (re-graded): a single path given as text is one file, not one file per letter
def test_documents_accepts_single_path_string(tmp_home, make_ctx):
    docs = tmp_home.home / "Documents"
    docs.mkdir()
    (docs / "a.txt").write_text("Tax notes")
    local = FakeProvider([json.dumps({"relevant": True, "summary": "tax"})], name="ollama")
    result = Documents().run("x", {"paths": str(docs / "a.txt"), "question": "tax"}, make_ctx(providers=[local]))
    assert len(local.calls) == 1 and len(result.data["summaries"]) == 1
