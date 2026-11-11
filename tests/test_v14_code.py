import os
import sys

import pytest

import prometheus.agents.code_agent as ca
from prometheus import sandbox

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the POSIX limits are tested here; Windows on CI")


def run(code, tmp_path, **kw):
    return sandbox.run_locked(code, sandbox.new_folder(tmp_path), **kw)


def test_ordinary_scripts_work_and_save_files(tmp_path):
    r = run("import statistics, csv\nprint(statistics.mean([2, 4, 9]))\nopen('marks.csv', 'w').write('a,b\\n1,2\\n')",
            tmp_path)
    assert r.ok and r.output.strip() == "5" and [f.name for f in r.files] == ["marks.csv"]


@pytest.mark.parametrize("code, why", [
    ("import subprocess\nsubprocess.run(['echo', 'hi'])", "starting other programs"),
    ("import os\nos.system('echo hi')", "starting other programs"),
    ("open(os.path.expanduser('~') + '/../x.txt', 'w')", "save files"),
    ("print(open(HOME + '/.bashrc').read())", "read files"),
    ("print(os.listdir(HOME))", "look inside"),
])
def test_dangerous_things_are_blocked(tmp_path, code, why):
    from pathlib import Path

    r = run(f"import os\nHOME = {str(Path.home())!r}\n" + code, tmp_path)
    assert not r.ok and why in r.output and "_prometheus_guard" not in r.output


def test_no_internet_unless_allowed_and_no_keys(tmp_path, monkeypatch):
    r = run("import socket\nsocket.create_connection(('example.com', 80), timeout=2)", tmp_path)
    assert not r.ok and r.needs_network
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    r = run("import os\nprint(os.environ.get('ANTHROPIC_API_KEY'))", tmp_path)
    assert r.output.strip() == "None"


def test_time_limit(tmp_path):
    r = run("while True: pass", tmp_path, timeout=2)
    assert r.timed_out and not r.ok and "stopped it" in r.describe()


def test_agent_shows_the_script_and_runs_after_yes(make_ctx, monkeypatch, tmp_path):
    ctx = make_ctx()
    added = []
    services = type("S", (), {})()
    services.store, services.approvals = ctx.store, ctx.approvals
    services.settings = type("Cfg", (), {"code_windows_sandbox": False})()
    monkeypatch.setattr(ca, "SERVICES", services)
    monkeypatch.setattr(ctx.store, "add_output", lambda task, path, kind, is_dir=False: added.append(path))
    ctx.approvals.register("code_run", ca.run_handler)
    ctx.approvals.register("code_network", ca.run_handler)
    res = ca.CodeAgent().run("x", {"code": "```python\nprint(6 * 7)\nopen('a.txt','w').write('hi')\n```"}, ctx)
    assert res.status == "waiting_approval"
    ask = ctx.store.asks_for_chat(ctx.chat_id())[0]
    assert "print(6 * 7)" in ask.summary
    out = ctx.approvals.resolve(ask.id, approved=True)
    assert "42" in out and len(added) == 1 and added[0].endswith("a.txt")
    # a script that needs the internet asks again, for one run
    res = ca.CodeAgent().run("x", {"code": "import urllib.request\nurllib.request.urlopen('https://example.com')"}, ctx)
    ask = [a for a in ctx.store.asks_for_chat(ctx.chat_id()) if a.kind == "code_run"][-1]
    out = ctx.approvals.resolve(ask.id, approved=True)
    assert "needs the internet" in out
    assert any(a.kind == "code_network" for a in ctx.store.asks_for_chat(ctx.chat_id()))
