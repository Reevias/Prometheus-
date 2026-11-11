import sys

import pytest

import prometheus.agents.tools_agent as ta
from prometheus.own_tools import OwnTools
from tests.conftest import login

CODE = """import json
args = json.load(open('inputs.json'))
words = args['text'].split()
print(len(words), 'words')
open('upper.txt', 'w').write(args['text'].upper())
"""


@pytest.fixture
def tools(tmp_home, monkeypatch):
    t = OwnTools(tmp_home.data / "tools")
    monkeypatch.setattr(ta, "TOOLS", t)
    monkeypatch.setattr(ta, "SERVICES", None)
    return t


@pytest.mark.skipif(sys.platform == "win32", reason="sandbox limits are tested on Linux here")
def test_make_approve_once_then_run_freely(tools, make_ctx, monkeypatch):
    ctx = make_ctx()
    ctx.approvals.register("approve_tool", ta.approve_handler)
    agent = ta.ToolsAgent()
    res = agent.run("x", {"action": "make", "name": "word_count", "description": "Counts words and shouts them",
                          "inputs": ["text"], "code": CODE}, ctx)
    assert res.status == "waiting_approval"
    ran = agent.run("x", {"action": "run", "name": "word_count", "inputs": {"text": "a b c"}}, ctx)
    assert ran.status == "failed" and "isn't approved" in ran.summary
    approval = [a for a in ctx.store.big_approvals() if a.kind == "approve_tool"][0]
    assert "json.load" in approval.payload["diff"]
    assert "Approved" in ctx.approvals.resolve(approval.id, approved=True)
    assert "word_count" in tools.catalog()
    out = agent.run("x", {"action": "run", "name": "word_count", "inputs": '{"text": "hello there world"}'}, ctx)
    assert out.status == "done" and "3 words" in out.summary and out.data["made"][0].endswith("upper.txt")
    assert tools.get("word_count")["runs"] == 1
    # changed code needs approving again
    agent.run("x", {"action": "make", "name": "word_count", "description": "d", "inputs": ["text"],
                    "code": CODE + "print('more')\n"}, ctx)
    assert agent.run("x", {"action": "run", "name": "word_count", "inputs": {"text": "a"}}, ctx).status == "failed"
    assert "word_count" not in tools.catalog()


def test_approval_is_for_the_exact_code_seen(tools):
    tools.save_draft("my_tool", "d", "print(1)", [])
    from prometheus.own_tools import digest

    seen = digest("print(1)\n")
    tools.save_draft("my_tool", "d", "print(2)", [])            # changed after the card was shown
    assert not tools.approve("my_tool", seen)
    with pytest.raises(Exception):
        tools.save_draft("../evil", "d", "print(1)", [])


def test_skills_page_lists_and_removes(app_client, tools):
    client, _ = app_client()
    h = {"X-CSRF-Token": login(client)}
    tools.save_draft("convert_heic", "Turns HEIC photos into JPG", "print('hi')", ["paths"])
    assert "convert_heic" in client.get("/skills").text
    client.post("/tools/convert_heic/remove", headers=h)
    assert tools.get("convert_heic") is None
