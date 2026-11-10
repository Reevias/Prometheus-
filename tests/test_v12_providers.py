"""v1.2: catalog of free providers, a generic connector, opt-in routing, searchable settings."""
import json
import re

import httpx
import pytest

from conftest import login

from prometheus.catalog import load_catalog
from prometheus.config import KEY_NAMES, Settings, get_secret, set_secret
from prometheus.memory.store import Store
from prometheus.models.base import PrivacyViolation, ProviderUnavailable
from prometheus.models.compat import OpenAICompatProvider
from prometheus.models.router import build_router
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch


def info(pid):
    return {p.id: p for p in load_catalog()}[pid]


def test_catalog_contents():
    cat = {p.id: p for p in load_catalog()}
    assert len(cat) >= 12 and "openrouter" not in cat          # OpenRouter has its own connector
    assert cat["groq"].base_url == "https://api.groq.com/openai/v1" and cat["groq"].key_name == "GROQ_API_KEY"
    assert cat["groq"].models and not cat["groq"].keyless
    assert {pid for pid, p in cat.items() if p.keyless} == {"kilo-code", "llm7-io", "ovhcloud-ai-endpoints"}
    assert cat["gemini"].key_name == "GEMINI_API_KEY" and cat["gemini"].base_url.endswith("/openai")
    assert not cat["cohere"].supported and cat["cohere"].why_unsupported
    assert cat["gemini"].logs_prompts and cat["mistral-ai"].logs_prompts
    for p in cat.values():
        assert re.fullmatch(r"[a-z0-9-]+", p.id) and re.fullmatch(r"[A-Z0-9_]+_API_KEY", p.key_name)
        assert p.key_name in KEY_NAMES


def compat(pid, handler, **kw):
    return OpenAICompatProvider(info(pid), http=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def ok_handler(seen):
    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 3, "completion_tokens": 2}})
    return handler


def test_compat_request_with_key(tmp_home):
    set_secret("GROQ_API_KEY", "gsk_test_1234")
    seen = {}
    p = compat("groq", ok_handler(seen), enabled=True)
    assert p.available() and p.is_local is False and p.name == "groq"
    reply = p.complete([{"role": "user", "content": "q"}], "sys", model="openai/gpt-oss-120b")
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["auth"] == "Bearer gsk_test_1234" and seen["body"]["model"] == "openai/gpt-oss-120b"
    assert reply.text == "hi" and reply.usd == 0 and reply.tokens_in == 3


def test_keyless_provider_sends_no_key_and_must_be_switched_on(tmp_home, monkeypatch):
    monkeypatch.delenv("LLM7_IO_API_KEY", raising=False)
    seen = {}
    assert compat("llm7-io", ok_handler(seen)).available() is False          # off until the user turns it on
    p = compat("llm7-io", ok_handler(seen), enabled=True)
    assert p.available()
    p.complete([{"role": "user", "content": "q"}], "s")
    assert seen["auth"] is None and seen["body"]["model"] == info("llm7-io").models[0]


def test_needs_key_or_unsupported_is_unavailable(tmp_home, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    assert compat("groq", ok_handler({}), enabled=True).available() is False
    assert compat("cohere", ok_handler({}), enabled=True).available() is False


@pytest.mark.parametrize("status", [401, 429, 500])
def test_compat_errors_hide_the_key(tmp_home, status):
    set_secret("GROQ_API_KEY", "gsk_test_1234")
    p = compat("groq", lambda r: httpx.Response(status, text="bad key gsk_test_1234"), enabled=True)
    with pytest.raises(ProviderUnavailable) as err:
        p.complete([{"role": "user", "content": "q"}], "s")
    assert "gsk_test_1234" not in str(err.value) and "Groq" in str(err.value)


def router_for(tmp_home, settings):
    return build_router(settings, Budget(Store(tmp_home.data / "m.db"), 5.0), KillSwitch())


def test_enabled_providers_join_cloud_routes_but_never_private(tmp_home):
    settings = Settings(enabled_providers=["groq", "llm7-io"], provider_models={"groq": "openai/gpt-oss-120b"})
    router = router_for(tmp_home, settings)
    assert "groq" in router.providers and "kilo-code" in router.providers
    for purpose in ("plan", "combine", "research"):
        assert "groq:openai/gpt-oss-120b" in router.routes[purpose] and "llm7-io" in router.routes[purpose]
        assert "kilo-code" not in router.routes[purpose]
    assert router.routes["private"] == ["ollama"]
    assert router.providers["groq"].enabled and not router.providers["kilo-code"].enabled


def test_free_provider_can_never_be_a_private_route(tmp_home):
    with pytest.raises(PrivacyViolation):
        router_for(tmp_home, Settings(enabled_providers=["groq"],
                                      routes={"plan": ["ollama"], "private": ["groq"]}))


def authed(app_client):
    client, services = app_client()
    return client, services, {"X-CSRF-Token": login(client)}


def test_settings_lists_providers_with_search_box(app_client):
    client, _, _ = authed(app_client)
    page = client.get("/settings").text
    assert 'id="provider-search"' in page
    for name in ("Groq", "Mistral AI", "LLM7.io", "Ollama", "OpenRouter", "NVIDIA NIM"):
        assert name in page
    assert "no key needed" in page.lower() and "may log" in page.lower()
    assert not re.search(r'(src|href)=["\']https?://', page)
    assert page.index("Ollama") < page.index("Cohere")           # ready/usable ones are listed before unsupported


def test_turning_a_provider_on_and_saving_its_key(app_client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client, services, h = authed(app_client)
    resp = client.post("/settings/providers", data={"id": "llm7-io", "enabled": "1", "confirm": "1", "model": ""},
                       headers=h)
    assert resp.status_code == 200 and services.settings.enabled_providers == ["llm7-io"]
    client.post("/settings/keys", data={"name": "GROQ_API_KEY", "value": "gsk_test_1234",
                                        "password": "correct horse battery"}, headers=h)
    assert get_secret("GROQ_API_KEY") == "gsk_test_1234"
    assert "groq" not in services.settings.enabled_providers        # a saved key alone does not switch it on
    client.post("/settings/providers", data={"id": "llm7-io", "enabled": "", "model": ""}, headers=h)
    assert "llm7-io" not in services.settings.enabled_providers
    bad = client.post("/settings/providers", data={"id": "nope", "enabled": "1", "confirm": "1", "model": ""},
                      headers=h)
    assert bad.status_code == 400
    assert "gsk_test_1234" not in client.get("/settings").text
