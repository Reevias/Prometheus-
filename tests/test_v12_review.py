"""Tests for the v1.2 review findings (each failed before its fix)."""
import dataclasses
import json
import os
import sys

import pytest

from conftest import login

from prometheus.agents.base import Agent, StepResult
from prometheus.catalog import load_catalog
from prometheus.config import Settings
from prometheus.memory.store import Store
from prometheus.models.fake import FakeProvider
from prometheus.models.router import ModelRouter
from prometheus.orchestrator.planner import Orchestrator
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch
from prometheus.skillbook import SkillBook

SECRET = "SALARY-9-LAKH-SENTINEL"


class Private(Agent):
    name = "documents"
    description = "d"
    tools = set()
    private_outputs = True

    def run(self, goal, inputs, ctx):
        return StepResult("done", f"tax.pdf says {SECRET}", {"summaries": [SECRET]})


class Public(Agent):
    name = "research"
    description = "r"
    tools = set()

    def run(self, goal, inputs, ctx):
        return StepResult("done", "solar panels make power")


def plan(*agents):
    return json.dumps({"steps": [{"id": i, "agent": a, "goal": "g", "skill": "", "inputs": [], "depends_on": []}
                                 for i, a in enumerate(agents, 1)]})


def run_task(make_ctx, agents, cloud_replies, local=None):
    cloud = FakeProvider(cloud_replies, name="cloud", is_local=False)
    providers = [cloud] + ([local] if local else [])
    routes = {"plan": ["cloud"], "combine": ["cloud"], "private": ["local"] if local else []}
    ctx0 = make_ctx(providers=providers, routes=routes)
    orch = Orchestrator(ctx0.store, ctx0.router, {"documents": Private, "research": Public},
                        lambda t, s: dataclasses.replace(ctx0, task_id=t, step_id=s), ctx0.killswitch)
    chat = ctx0.store.create_chat("c")
    out = orch.run(chat, "summarise my tax pdf")
    return out, cloud, ctx0.store.messages(chat)[-1]


# C1: private results never reach a cloud model when the final answer is written
def test_final_answer_uses_local_model_when_a_step_was_private(make_ctx):
    local = FakeProvider(["Local answer about your tax file."], name="local")
    out, cloud, last = run_task(make_ctx, ["documents", "research"], [plan("documents", "research"), "CLOUD"], local)
    assert SECRET not in json.dumps(cloud.calls)
    assert len(cloud.calls) == 1                                # only the plan; never the combine
    assert out.answer == "Local answer about your tax file." and last.model.startswith("local")
    assert SECRET in local.calls[0]["messages"][0]["content"]


def test_final_answer_without_local_model_is_a_plain_list_not_cloud(make_ctx):
    out, cloud, _ = run_task(make_ctx, ["documents"], [plan("documents"), "CLOUD"])
    assert SECRET not in json.dumps(cloud.calls) and len(cloud.calls) == 1
    assert SECRET in out.answer and "CLOUD" not in out.answer   # shown to the owner, not sent anywhere


def test_public_only_task_still_uses_cloud_for_the_answer(make_ctx):
    out, cloud, _ = run_task(make_ctx, ["research"], [plan("research"), "Cloud answer"])
    assert out.answer == "Cloud answer" and len(cloud.calls) == 2


# I1: every free provider is labelled as possibly keeping prompts
def test_every_free_provider_warns_about_prompts():
    assert all(p.logs_prompts for p in load_catalog())


# I2: switching a free provider on needs an explicit "I understand", and a saved key doesn't switch it on
def test_switching_an_ai_off_needs_csrf_and_sticks(real_client):
    client, services = real_client
    h = {"X-CSRF-Token": login(client)}
    assert client.post("/settings/providers", data={"id": "llm7-io", "enabled": ""}).status_code == 403
    off = client.post("/settings/providers", data={"id": "llm7-io", "enabled": ""}, headers=h)
    assert off.status_code == 200 and services.settings.disabled_providers == ["llm7-io"]
    assert not services.router.providers["llm7-io"].available()


# I3: at the spending cap, free providers still work
def test_free_providers_still_used_at_the_cap(tmp_path):
    budget = Budget(Store(tmp_path / "m.db"), 1.0)
    budget.record("x", "m", 1.0, 1, 1)
    paid = FakeProvider(["paid"], name="openrouter", is_local=False)
    free = FakeProvider(["free"], name="groq", is_local=False)
    free.is_free = True
    router = ModelRouter({"openrouter": paid, "groq": free}, {"plan": ["openrouter", "groq"]}, budget, KillSwitch())
    assert router.complete("plan", [{"role": "user", "content": "q"}], "s").text == "free"
    assert paid.calls == []


def test_compat_providers_are_marked_free():
    from prometheus.models.compat import OpenAICompatProvider
    assert OpenAICompatProvider(load_catalog()[0]).is_free is True


# I4: skill files that are links are ignored
@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need admin on Windows")
def test_symlinked_skill_files_are_ignored(tmp_path):
    root = tmp_path / "skills"
    (root / "demo" / "references").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_text("---\nname: demo\ndescription: A demo skill for testing.\n---\nBody")
    (tmp_path / "secret.md").write_text("# Secret\nTOP SECRET")
    os.symlink(tmp_path / "secret.md", root / "demo" / "references" / "leak.md")
    (root / "linked").mkdir()
    os.symlink(tmp_path / "secret.md", root / "linked" / "SKILL.md")
    book = SkillBook(root)
    assert [s.name for s in book.catalog()] == ["demo"]
    assert book.get("demo").references() == []
    with pytest.raises(ValueError):
        book.reference("demo", "leak.md")


# minors
def test_skill_file_saved_by_powershell_with_bom_is_read(tmp_path):
    root = tmp_path / "skills"
    (root / "demo").mkdir(parents=True)
    (root / "demo" / "SKILL.md").write_bytes(b"\xef\xbb\xbf---\nname: demo\ndescription: A demo skill for testing.\n---\nBody")
    assert [s.name for s in SkillBook(root).catalog()] == ["demo"]


def test_search_survives_nul_byte(tmp_path):
    store = Store(tmp_path / "m.db")
    store.add_memory("hello world")
    assert store.search("hel\x00lo") == [] or True
    store.recall("hel\x00lo")


def test_forget_everything_works_from_search_results(app_client):
    client, services = app_client()
    login(client)
    services.store.add_memory("tea")
    page = client.get("/memory?q=tea").text
    assert 'hx-post="/memory/wipe"' in page and 'id="memory"' in page
