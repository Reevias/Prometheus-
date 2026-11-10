import json
import os
from pathlib import Path

import docx
import pytest

from prometheus.agents.base import StepResult
from prometheus.agents.files import Files
from prometheus.models.fake import FakeProvider
from prometheus.models.router import ModelRouter, head_choices
from prometheus.orchestrator.planner import resolve_inputs
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch
from tests.conftest import login


@pytest.fixture
def ctx(make_ctx):
    trashed = []
    c = make_ctx(trash=lambda p: (trashed.append(p), os.remove(p)))
    c.trashed = trashed
    return c


def test_create_text_markdown_and_word(ctx, tmp_home):
    res = Files().run("t", {"action": "create", "path": "Documents/plan.md", "content": "# Plan\n- one"}, ctx)
    assert res.status == "done" and (tmp_home.home / "Documents/plan.md").read_text() == "# Plan\n- one"
    res = Files().run("t", {"action": "create", "path": "shopping", "content": "milk"}, ctx)   # name only
    assert (tmp_home.home / "Documents/shopping.txt").read_text() == "milk"
    Files().run("t", {"action": "create", "path": "Desktop/Roadmap.docx",
                      "content": "# CS roadmap\nIntro line\n- Learn Python\n1. Stage one"}, ctx)
    d = docx.Document(str(tmp_home.home / "Desktop/Roadmap.docx"))
    assert [p.text for p in d.paragraphs] == ["CS roadmap", "Intro line", "Learn Python", "Stage one"]
    assert d.paragraphs[0].style.name.startswith("Heading")


def test_create_never_overwrites_and_undo_recycles(ctx, tmp_home):
    (tmp_home.home / "Documents").mkdir()
    (tmp_home.home / "Documents/notes.txt").write_text("mine")
    res = Files().run("t", {"action": "create", "path": "Documents/notes.txt", "content": "new"}, ctx)
    assert (tmp_home.home / "Documents/notes.txt").read_text() == "mine"
    assert (tmp_home.home / "Documents/notes (1).txt").read_text() == "new"
    assert res.data["paths"] == [str(tmp_home.home / "Documents/notes (1).txt")]
    for r in ctx.files.journal.runs():
        ctx.files.undo_run(r["id"])
    assert not (tmp_home.home / "Documents/notes (1).txt").exists() and ctx.trashed
    assert (tmp_home.home / "Documents/notes.txt").read_text() == "mine"


@pytest.mark.parametrize("name", ["run.bat", "x.ps1", "a.exe", "b.vbs", "c.js", "d.lnk", "e.reg", "f.hta"])
def test_never_creates_runnable_files(ctx, tmp_home, name):
    res = Files().run("t", {"action": "create", "path": f"Desktop/{name}", "content": "calc"}, ctx)
    assert res.status == "failed" and not (tmp_home.home / "Desktop" / name).exists()


def test_create_refuses_blocked_places(ctx, tmp_home):
    assert Files().run("t", {"action": "create", "path": "~/.ssh/config", "content": "x"}, ctx).status == "failed"
    assert Files().run("t", {"action": "create", "path": "/etc/x.txt", "content": "x"}, ctx).status == "failed"


def test_step_summary_can_be_saved_to_a_file():
    results = {1: StepResult("done", "Research says solar is cheap [1].", {"sources": ["u"]})}
    assert resolve_inputs({"content": "$step1.summary"}, results) == {"content": "Research says solar is cheap [1]."}
    assert resolve_inputs({"s": "$step1.sources"}, results) == {"s": ["u"]}


def router_with(store, head=""):
    paid = FakeProvider(["paid answer"], name="anthropic", is_local=False, usd=0.01)
    cheap = FakeProvider(["cheap answer"], name="groq", is_local=False)
    cheap.is_free = True
    local = FakeProvider(["local answer"], name="ollama")
    r = ModelRouter({"anthropic": paid, "groq": cheap, "ollama": local},
                    {"plan": ["anthropic", "groq", "ollama"], "combine": ["anthropic", "ollama"],
                     "research": ["anthropic", "ollama"], "private": ["ollama"]}, Budget(store, 5.0), KillSwitch())
    r.head = head
    return r, paid, cheap


def test_chosen_brain_is_used_and_never_falls_back_to_paid(tmp_path):
    from prometheus.memory.store import Store
    from prometheus.models.base import ProviderUnavailable

    store = Store(tmp_path / "m.db")
    r, paid, cheap = router_with(store, head="groq")
    assert r.complete("plan", [], "s").text == "cheap answer"
    assert r.entries_for("plan") == ["groq", "ollama"]                 # paid Claude dropped
    assert r.entries_for("research") == ["anthropic", "ollama"]         # research untouched
    r.head = "anthropic:claude-haiku-5-5"
    assert r.entries_for("plan") == ["anthropic:claude-haiku-5-5", "groq", "ollama"]

    def down(*a, **k):
        raise ProviderUnavailable("down")
    paid.complete = down
    assert r.complete("plan", [], "s").text == "cheap answer"           # free fallback only
    r.head = ""
    assert r.entries_for("plan") == ["anthropic", "groq", "ollama"]


def test_head_choices_list_only_ready_models(tmp_path):
    from prometheus.memory.store import Store

    r, paid, cheap = router_with(Store(tmp_path / "m.db"))
    labels = dict(head_choices(r))
    assert "anthropic:claude-haiku-5-5" in labels and "anthropic:claude-opus-5-5" in labels
    cheap.available = lambda: False
    assert not any(e.startswith("groq") for e in dict(head_choices(r)))


def test_brain_picker_in_chat_and_settings(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    cid = services.store.create_chat("c")
    page = client.get(f"/chats/{cid}").text
    assert 'name="head"' in page and "Auto (best available)" in page
    choices = [e for e, _ in head_choices(services.router)]
    assert client.post("/settings/head", data={"head": choices[0]}, headers=h).status_code == 204
    assert services.settings.head_model == choices[0] and services.router.head == choices[0]
    assert client.post("/settings/head", data={"head": "evil:model"}, headers=h).status_code == 400
    assert client.post("/settings/head", data={"head": ""}).status_code == 403          # needs CSRF
    client.post("/settings/head", data={"head": ""}, headers=h)
    assert services.router.head == "" and 'name="head"' in client.get("/settings").text
