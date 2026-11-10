"""Tests for the v1.3 security review findings (each failed before its fix)."""
import dataclasses
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from prometheus import laptop as laptop_mod
from prometheus.agents.antigravity import AntigravityRunner, run_antigravity_job
from prometheus.agents.base import Agent, StepResult
from prometheus.agents.system import SystemAgent, perform, wifi_password_handler
from prometheus.laptop import Laptop, LaptopError, sweep_wifi_profiles
from prometheus.memory.store import Store
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.planner import Orchestrator
from prometheus.safety.approvals import Approvals, TryAgain
from prometheus.safety.budget import Budget, BudgetExceeded
from prometheus.safety.killswitch import KillSwitch


# 1: answers saved before the upgrade count as private
def test_old_assistant_messages_become_private_on_upgrade(tmp_path):
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("""
        CREATE TABLE messages (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
            created REAL NOT NULL, model TEXT NOT NULL DEFAULT '');
        INSERT INTO messages (chat_id, role, text, created) VALUES (1, 'user', 'my question', 0);
        INSERT INTO messages (chat_id, role, text, created) VALUES (1, 'assistant', 'tax.pdf says SALARY', 0);
    """)
    db.commit()
    db.close()
    store = Store(tmp_path / "old.db")
    user, assistant = store.messages(1)
    assert assistant.private is True and user.private is False
    store.add_message(1, "assistant", "fresh direct answer")
    assert store.messages(1)[-1].private is False
    assert [m.private for m in Store(tmp_path / "old.db").messages(1)] == [False, True, False]   # not redone on restart


# 2: Antigravity respects the cap, estimates cost, can be stopped and cancelled
class Google:
    def __init__(self, fail=False, statuses=("in_progress", "completed")):
        self.cancelled, self.fail = [], fail
        self._statuses = iter(statuses)
        self.interactions = SimpleNamespace(create=self._create, get=self._get, cancel=lambda id: self.cancelled.append(id))

    def _create(self, **kw):
        return SimpleNamespace(id="int_1", status="in_progress", output_text="", usage=None)

    def _get(self, id):
        if self.fail:
            raise RuntimeError("network died")
        status = next(self._statuses, "completed")
        return SimpleNamespace(id=id, status=status, output_text="done text",
                               usage=SimpleNamespace(total_tokens=500_000))


@pytest.fixture
def parts(tmp_path):
    store = Store(tmp_path / "m.db")
    return store, Budget(store, 1.0), KillSwitch()


def runner(parts, client):
    store, budget, ks = parts
    chat = store.create_chat("c")
    return AntigravityRunner(store, budget, ks, client_factory=lambda: client, sleep=lambda s: None,
                             run_in_thread=lambda fn: fn()), chat


def test_antigravity_refuses_to_start_at_the_cap(parts):
    store, budget, _ = parts
    budget.record("x", "m", 1.0, 1, 1)
    r, chat = runner(parts, Google())
    with pytest.raises(BudgetExceeded):
        r.submit({"task": "t", "model": "gemini-3.8-flash", "max_tokens": 1000, "chat_id": chat})


def test_antigravity_cost_is_estimated_and_counted(parts):
    store, budget, _ = parts
    r, chat = runner(parts, Google())
    r.submit({"task": "t", "model": "gemini-3.8-flash", "max_tokens": 1000, "chat_id": chat})
    assert budget.state().spent > 0
    assert "done text" in store.messages(chat)[-1].text


def test_antigravity_is_cancelled_on_error_and_on_stop(parts):
    store, budget, ks = parts
    client = Google(fail=True)
    r, chat = runner(parts, client)
    r.submit({"task": "t", "model": "gemini-3.8-flash", "max_tokens": 1000, "chat_id": chat})
    assert client.cancelled == ["int_1"] and "failed" in store.messages(chat)[-1].text.lower()
    ks.trigger("stop")
    client2 = Google(statuses=("in_progress", "in_progress"))
    out = run_antigravity_job({"task": "t", "model": "m", "max_tokens": 1}, client2, sleep=lambda s: None,
                              should_stop=lambda: ks.is_set)
    assert client2.cancelled == ["int_1"] and out["status"] == "cancelled"


# 3: an approval runs once even if clicked twice at the same time
def test_double_click_runs_the_handler_once(tmp_path):
    store = Store(tmp_path / "m.db")
    approvals = Approvals(store)
    ran = []

    def slow(payload):
        time.sleep(0.05)
        ran.append(1)
        return "done"

    approvals.register("k", slow)
    aid = approvals.request("k", "s", {})
    threads = [threading.Thread(target=lambda: approvals.resolve(aid, True)) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert ran == [1] and store.approval(aid).status == "approved"


# 4: a mistyped password does not burn the approval
def test_wrong_secret_keeps_the_approval_pending(tmp_path):
    store = Store(tmp_path / "m.db")
    approvals = Approvals(store)

    def handler(payload, secret):
        if len(secret) < 8:
            raise TryAgain("A Wi-Fi password has 8 to 63 characters.")
        return "Connected"

    approvals.register("wifi", handler, needs_secret=True)
    aid = approvals.request("wifi", "s", {"ssid": "x"}, secret_label="Wi-Fi password")
    assert "8 to 63" in approvals.resolve(aid, True, secret="short")
    assert store.approval(aid).status == "pending"
    assert approvals.resolve(aid, True, secret="longenough1") == "Connected"
    assert store.approval(aid).status == "approved"


def test_wifi_handler_asks_again_for_a_bad_password():
    with pytest.raises(TryAgain):
        wifi_password_handler({"ssid": "Office"}, "short", Laptop(run=lambda *a, **k: (0, ""), platform="win32"))


# 5: it only saves facts when you ask, and never secrets
def reply(answer, remember):
    return json.dumps({"answer": answer, "remember": remember, "steps": []})


@pytest.mark.parametrize("text,saved", [
    ("what is the weather like", 0), ("remember my accountant is Ravi", 1), ("Don't forget: my cat is called Momo", 1),
    ("please note that I live in Pune", 1), ("keep in mind I prefer metric units", 1)])
def test_memory_is_only_written_when_you_ask(make_ctx, text, saved):
    cloud = FakeProvider([reply("ok", ["A fact about the user"])], name="cloud", is_local=False)
    ctx0 = make_ctx(providers=[cloud], routes={"plan": ["cloud"], "combine": ["cloud"]})
    orch = Orchestrator(ctx0.store, ctx0.router, {}, lambda t, s: ctx0, ctx0.killswitch)
    orch.run(ctx0.store.create_chat("c"), text)
    assert len(ctx0.store.memories()) == saved


@pytest.mark.parametrize("fact", ["my api key is sk-ant-api03-abcdefghij", "password is hunter22", "token ghp_abcdefghijklmnopqrst",
                                  "my github_pat_11ABCDEFG0abcdef", "the pin is 4821 and the password: x"])
def test_secret_looking_facts_are_never_saved(make_ctx, fact):
    cloud = FakeProvider([reply("ok", [fact])], name="cloud", is_local=False)
    ctx0 = make_ctx(providers=[cloud], routes={"plan": ["cloud"], "combine": ["cloud"]})
    orch = Orchestrator(ctx0.store, ctx0.router, {}, lambda t, s: ctx0, ctx0.killswitch)
    orch.run(ctx0.store.create_chat("c"), "remember this please")
    assert ctx0.store.memories() == []


# 6: joining an open or unknown network always asks, and open profiles never auto-join
class Net:
    def __init__(self):
        self.calls, self.files = [], {}

    def __call__(self, argv, timeout=20, env=None):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        if "show profiles" in joined:
            return 0, "    All User Profile     : HomeNet\n"
        if "show networks" in joined:
            return 0, "SSID 1 : Cafe Free WiFi\n    Authentication : Open\n         Signal : 40%\n"
        if "add profile" in joined:
            path = next(a for a in argv if a.startswith("filename=")).split("=", 1)[1]
            self.files[path] = open(path, encoding="utf-8").read()
        return 0, "ok"


def test_open_wifi_asks_first_and_is_never_auto_join(make_ctx, tmp_path):
    net = Net()
    ctx = make_ctx()
    lap = Laptop(run=net, platform="win32")
    result = SystemAgent(laptop=lap).run("join", {"action": "wifi_connect", "ssid": "Cafe Free WiFi"}, ctx)
    assert result.status == "waiting_approval" and not any("add" in c for c in net.calls)
    [a] = ctx.store.approvals()
    assert a.kind == "laptop_action" and a.payload["action"] == "wifi_connect_open"
    perform("wifi_connect_open", {"target": "Cafe Free WiFi"}, lap)
    [xml] = net.files.values()
    assert "<connectionMode>manual</connectionMode>" in xml and "open" in xml


# 7: strict validation
@pytest.mark.parametrize("ssid", ["abc\n", "a\u2028b", "a\x7fb", "a\x85b", "\ud800x", "é" * 17, "x" * 33])
def test_ssid_must_match_fully(ssid):
    with pytest.raises(LaptopError):
        Laptop(run=Net(), platform="win32").wifi_connect_known(ssid)


@pytest.mark.parametrize("password", ["password1\n", "pass word\x00", "p" * 64, "short"])
def test_password_must_match_fully(password, tmp_path):
    with pytest.raises(LaptopError):
        Laptop(run=Net(), platform="win32").wifi_connect_new("Office", password, workdir=tmp_path)


def test_app_names_are_trimmed_and_then_checked_in_full():
    run = Net()
    lap = Laptop(run=run, platform="win32")
    envs = []
    lap.run = lambda argv, timeout=20, env=None: (envs.append(dict(env or {})) or (0, "opened"))
    lap.app_open("notepad\n")
    assert envs[-1]["PROM_APP"] == "notepad"           # no line break ever reaches the command
    for bad in ("note\npad", "x\r\ny"):
        with pytest.raises(LaptopError):
            lap.app_open(bad)


# 8 + 9: real Windows programs are used by full path, and output is read in the Windows console encoding
def test_windows_commands_use_system32_and_oem_decoding(monkeypatch):
    seen = {}

    def fake_run(argv, **kw):
        seen["argv"], seen["kw"] = argv, kw
        return subprocess.CompletedProcess(argv, 0, "out", "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    monkeypatch.setattr(laptop_mod.sys, "platform", "win32")
    monkeypatch.setenv("SystemRoot", r"C:\Windows")
    monkeypatch.setattr(laptop_mod.os.path, "isfile", lambda p: True)
    laptop_mod._run(["netsh", "wlan", "show", "profiles"])
    assert seen["argv"][0].lower().endswith(r"system32\netsh.exe") or seen["argv"][0].lower().endswith("system32/netsh.exe")
    assert seen["kw"]["encoding"] == "oem" and seen["kw"]["errors"] == "replace"
    laptop_mod._run(["powershell", "-NoProfile"])
    assert "windowspowershell" in seen["argv"][0].lower().replace("\\", "").replace("/", "")


# 11: the password file never survives, even if Windows won't delete it
def test_profile_file_is_wiped_even_if_it_cannot_be_deleted(tmp_path, monkeypatch):
    kept = {}

    def run(argv, timeout=20, env=None):
        if "add" in argv:
            kept["path"] = Path(next(a for a in argv if a.startswith("filename=")).split("=", 1)[1])
        return 0, "ok"

    def locked(self, missing_ok=False):
        raise PermissionError(13, "antivirus has it open")

    monkeypatch.setattr(Path, "unlink", locked)
    monkeypatch.setattr(laptop_mod.time, "sleep", lambda s: None)
    Laptop(run=run, platform="win32").wifi_connect_new("Office", "s3cretpassword", workdir=tmp_path)
    assert "s3cretpassword" not in kept["path"].read_text(encoding="utf-8", errors="ignore")


def test_leftover_profiles_are_swept_at_startup(tmp_path):
    (tmp_path / "prometheus-wifi-abc.xml").write_text("<key>secret</key>")
    (tmp_path / "other.xml").write_text("keep")
    sweep_wifi_profiles(tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["other.xml"]


# minors
def test_very_long_text_is_cut_before_formatting():
    from prometheus.web.render import render_message
    assert len(str(render_message("**x** " * 100_000))) < 200_000


def test_previous_shipped_planner_prompt_is_known():
    from prometheus.selfedit.prompts import KNOWN_OLD
    assert len(KNOWN_OLD["planner"]) >= 3
