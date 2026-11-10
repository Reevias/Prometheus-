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


def test_providers_with_a_key_or_no_key_join_cloud_routes_but_never_private(tmp_home, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_1234")
    monkeypatch.delenv("MISTRAL_AI_API_KEY", raising=False)
    router = router_for(tmp_home, Settings())
    for purpose in ("plan", "combine", "research"):
        names = [e.split(":")[0] for e in router.routes[purpose]]
        assert "groq" in names and "llm7-io" in names            # a key, and no key needed
        assert "mistral-ai" not in names                          # no key yet
        assert names.index("groq") < names.index("llm7-io") < names.index("ollama")
    assert router.routes["private"] == ["ollama"]
    off = router_for(tmp_home, Settings(free_no_key=False, disabled_providers=["groq"]))
    assert not any(e.split(":")[0] in ("groq", "llm7-io") for e in off.routes["plan"])


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
    for name in ("Groq", "Mistral AI", "LLM7.io", "Ollama", "OpenRouter", "NVIDIA NIM", "Claude"):
        assert name in page
    assert "free, no key" in page.lower() and "may keep prompts" in page.lower()
    assert 'name="model"' not in page                            # no model to choose
    assert not re.search(r'(src|href)=["\']https?://', page)
    assert page.index("Ollama") < page.index("Cohere")           # unsupported ones are listed last


def test_adding_a_key_switches_the_ai_on_at_once(real_client, monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client, services = real_client
    h = {"X-CSRF-Token": login(client)}
    assert not any(e.startswith("groq") for e in services.router.routes["plan"])
    resp = client.post("/settings/keys", data={"name": "GROQ_API_KEY", "value": "gsk_test_1234",
                                               "password": "correct horse battery"}, headers=h)
    assert resp.status_code == 200 and "Groq is on" in resp.text
    assert get_secret("GROQ_API_KEY") == "gsk_test_1234"
    assert any(e.startswith("groq") for e in services.router.routes["plan"])       # no restart, no model to pick
    assert services.router.providers["groq"].available()
    client.post("/settings/providers", data={"id": "groq", "enabled": ""}, headers=h)
    assert "groq" in services.settings.disabled_providers and not services.router.providers["groq"].available()
    client.post("/settings/providers", data={"id": "groq", "enabled": "1"}, headers=h)
    assert services.router.providers["groq"].available()
    assert client.post("/settings/providers", data={"id": "nope", "enabled": "1"}, headers=h).status_code == 400
    assert "gsk_test_1234" not in client.get("/settings").text
    client.post("/settings/keys/delete", data={"name": "GROQ_API_KEY", "password": "correct horse battery"},
                headers=h)
    assert not any(e.startswith("groq") for e in services.router.routes["plan"])


def test_free_no_key_switch(real_client):
    client, services = real_client
    h = {"X-CSRF-Token": login(client)}
    assert services.settings.free_no_key
    client.post("/settings/free", data={}, headers=h)
    assert not services.settings.free_no_key and not services.router.providers["llm7-io"].available()
    client.post("/settings/free", data={"on": "1"}, headers=h)
    assert services.router.providers["llm7-io"].available()
