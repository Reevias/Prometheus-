"""Coarena (owner, 16 Nov): a free-key AI that is used only when you pick it in the ask box; free use only."""
import json

import httpx

from prometheus.catalog import load_catalog
from prometheus.config import KEY_NAMES, Settings
from prometheus.memory.store import Store
from prometheus.models.compat import OpenAICompatProvider
from prometheus.models.router import build_router, head_choices
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch


def coarena():
    return {p.id: p for p in load_catalog()}["coarena"]


def router_for(tmp_home, settings):
    return build_router(settings, Budget(Store(tmp_home.data / "m.db"), 5.0), KillSwitch())


def test_in_the_catalog_with_its_free_key():
    c = coarena()
    assert c.base_url == "https://coarena.ai/api/v1" and c.key_name == "COARENA_API_KEY" and not c.keyless
    assert c.pick_only and c.supported and c.logs_prompts and "gemini-3-8-flash" in c.models
    assert c.signup_url == "https://coarena.ai/account/api" and KEY_NAMES["COARENA_API_KEY"] == "Coarena"


def test_never_used_by_itself_only_when_picked(tmp_home, monkeypatch):
    monkeypatch.setenv("COARENA_API_KEY", "ck_test_1234")
    router = router_for(tmp_home, Settings())
    for purpose, entries in router.routes.items():
        assert not any(e.split(":")[0] == "coarena" for e in entries), purpose
    picks = [e for e, _ in head_choices(router) if e.startswith("coarena:")]
    assert "coarena:gemini-3-8-flash" in picks and len(picks) == len(coarena().models)
    router.head = "coarena:gemini-3-8-flash"
    assert router.entries_for("plan")[0] == "coarena:gemini-3-8-flash" and router.is_free("coarena:gemini-3-8-flash")


def test_not_in_the_picker_without_a_key(tmp_home, monkeypatch):
    monkeypatch.delenv("COARENA_API_KEY", raising=False)
    assert not [e for e, _ in head_choices(router_for(tmp_home, Settings())) if e.startswith("coarena")]


def test_calls_coarena_like_any_openai_style_ai(tmp_home, monkeypatch):
    monkeypatch.setenv("COARENA_API_KEY", "ck_test_1234")
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers.get("authorization"), body=json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}})
    p = OpenAICompatProvider(coarena(), enabled=True, http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert p.complete([{"role": "user", "content": "q"}], "s", model="gemini-3-8-flash").text == "hi"
    assert seen["url"] == "https://coarena.ai/api/v1/chat/completions" and seen["auth"] == "Bearer ck_test_1234"
    assert seen["body"]["model"] == "gemini-3-8-flash"


def test_key_box_in_settings(app_client):
    from conftest import login

    client, services = app_client()
    login(client)
    page = client.get("/settings").text
    assert "Coarena" in page and "COARENA_API_KEY" in page and "https://coarena.ai/account/api" in page
