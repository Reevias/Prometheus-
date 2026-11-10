"""v1.1: key vault, OpenRouter, automatic model switching."""
import json

import httpx
import pytest

from prometheus.config import (KEY_NAMES, Settings, data_dir, delete_secret, get_secret, load_settings,
                               save_settings, secrets_set, set_secret)
from prometheus.memory.store import Store
from prometheus.models.base import PrivacyViolation, ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.models.openrouter import OpenRouterProvider
from prometheus.models.router import ModelRouter, build_router
from prometheus.safety.budget import Budget, BudgetExceeded
from prometheus.safety.killswitch import KillSwitch


# -- key vault ---------------------------------------------------------------
def test_set_secret_saved_and_wins_over_environment(tmp_home, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    set_secret("OPENROUTER_API_KEY", "sk-or-test-1")
    assert get_secret("OPENROUTER_API_KEY") == "sk-or-test-1"
    set_secret("OPENROUTER_API_KEY", "sk-or-test-2")
    assert get_secret("OPENROUTER_API_KEY") == "sk-or-test-2"
    assert (data_dir() / ".env").read_text().count("OPENROUTER_API_KEY") == 1
    delete_secret("OPENROUTER_API_KEY")
    assert get_secret("OPENROUTER_API_KEY") == "from-env"


def test_set_secret_rejects_unknown_names_and_bad_values(tmp_home):
    with pytest.raises(ValueError):
        set_secret("PATH", "x")
    with pytest.raises(ValueError):
        set_secret("OPENROUTER_API_KEY", "line1\nANTHROPIC_API_KEY=evil")
    with pytest.raises(ValueError):
        set_secret("OPENROUTER_API_KEY", "")
    assert "OPENROUTER_API_KEY" in KEY_NAMES


def test_secrets_set_reports_names_only(tmp_home, monkeypatch):
    for name in KEY_NAMES:
        monkeypatch.delenv(name, raising=False)
    set_secret("GITHUB_TOKEN", "ghp_test")
    status = secrets_set()
    assert status["GITHUB_TOKEN"] is True and status["OPENROUTER_API_KEY"] is False
    assert "ghp_test" not in json.dumps(status)


def test_old_settings_gain_openrouter_route(tmp_home):
    old = Settings(routes={"plan": ["anthropic", "ollama"], "combine": ["anthropic", "ollama"],
                           "research": ["anthropic", "ollama"], "private": ["ollama"]})
    save_settings(old)
    routes = load_settings().routes
    assert "openrouter" in routes["plan"] and routes["private"] == ["ollama"]


# -- OpenRouter ----------------------------------------------------------------
def openrouter(handler, **kw):
    return OpenRouterProvider(http=httpx.Client(transport=httpx.MockTransport(handler)), **kw)


def test_openrouter_request_shape(tmp_home):
    set_secret("OPENROUTER_API_KEY", "sk-or-test")
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "vendor/model-x", "choices": [{"message": {"content": "hi"}}],
                                         "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.002}})

    p = openrouter(handler, model="vendor/model-x")
    schema = {"type": "object", "properties": {}, "additionalProperties": False}
    reply = p.complete([{"role": "user", "content": "q"}], "sys", json_schema=schema)
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-or-test"
    assert seen["body"]["model"] == "vendor/model-x"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert seen["body"]["response_format"]["json_schema"]["schema"] == schema
    assert reply.text == "hi" and reply.usd == pytest.approx(0.002) and reply.model == "vendor/model-x"
    assert p.is_local is False and p.available()


def test_openrouter_model_override_and_schema_fallback(tmp_home):
    set_secret("OPENROUTER_API_KEY", "sk-or-test")
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "response_format" in body:
            return httpx.Response(400, json={"error": {"message": "response_format not supported"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}], "usage": {}})

    reply = openrouter(handler).complete([{"role": "user", "content": "q"}], "s", json_schema={"type": "object"},
                                        model="cheap/model")
    assert [b["model"] for b in bodies] == ["cheap/model", "cheap/model"]
    assert "response_format" not in bodies[1] and "JSON" in bodies[1]["messages"][0]["content"]
    assert reply.text == "{}"


@pytest.mark.parametrize("status", [401, 402, 429, 500])
def test_openrouter_errors_become_unavailable(tmp_home, status):
    set_secret("OPENROUTER_API_KEY", "sk-or-test")
    p = openrouter(lambda r: httpx.Response(status, json={"error": {"message": "nope sk-or-test"}}))
    with pytest.raises(ProviderUnavailable) as err:
        p.complete([{"role": "user", "content": "q"}], "s")
    assert "sk-or-test" not in str(err.value)


def test_openrouter_unavailable_without_key(tmp_home, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert openrouter(lambda r: httpx.Response(200)).available() is False


# -- automatic switching --------------------------------------------------------
class Flaky(FakeProvider):
    def __init__(self, *a, fail=1, **k):
        super().__init__(*a, **k)
        self.fail = fail

    def complete(self, messages, system, json_schema=None, max_tokens=4096, model=None):
        if self.fail > 0:
            self.fail -= 1
            self.calls.append("failed")
            raise ProviderUnavailable("rate limited")
        return super().complete(messages, system, json_schema, max_tokens, model)


def router_with(tmp_path, providers, routes, cap=5.0, clock=None):
    budget = Budget(Store(tmp_path / "m.db"), cap)
    extra = {"clock": clock} if clock else {}
    return ModelRouter({p.name: p for p in providers}, routes, budget, KillSwitch(), **extra), budget


MSG = [{"role": "user", "content": "q"}]


def test_failing_model_rests_then_is_retried(tmp_path):
    now = [1000.0]
    a = Flaky(["from a"], name="a", is_local=False, fail=1)
    b = FakeProvider(["from b"], name="b")
    router, _ = router_with(tmp_path, [a, b], {"plan": ["a", "b"]}, clock=lambda: now[0])
    assert router.complete("plan", MSG, "s").provider == "b"
    assert router.complete("plan", MSG, "s").provider == "b"      # a is resting: not even tried
    assert a.calls == ["failed"]
    resting = {r["name"]: r for r in router.status_detail()}
    assert resting["a"]["resting"] and "rate limited" in resting["a"]["reason"]
    now[0] += 301
    assert router.complete("plan", MSG, "s").provider == "a"      # rested: tried again


def test_budget_cap_drops_to_free_models(tmp_path):
    paid = FakeProvider(["paid"], name="paid", is_local=False, usd=1.0)
    local = FakeProvider(["local"], name="local")
    router, budget = router_with(tmp_path, [paid, local], {"plan": ["paid", "local"]}, cap=1.0)
    assert router.complete("plan", MSG, "s").provider == "paid"
    assert budget.state().stopped
    assert router.complete("plan", MSG, "s").provider == "local"  # cap reached: switches, doesn't stop
    only_paid, _ = router_with(tmp_path, [paid], {"plan": ["paid"]}, cap=1.0)
    with pytest.raises(BudgetExceeded):
        only_paid.complete("plan", MSG, "s")


def test_route_entry_can_name_a_model(tmp_path):
    p = FakeProvider(["x"], name="openrouter", is_local=False)
    router, _ = router_with(tmp_path, [p], {"plan": ["openrouter:big/model"], "research": ["openrouter:small/model:free"]})
    router.complete("plan", MSG, "s")
    router.complete("research", MSG, "s")
    assert [c["model"] for c in p.calls] == ["big/model", "small/model:free"]


def test_private_route_still_refuses_cloud_models(tmp_path):
    cloud = FakeProvider(["x"], name="openrouter", is_local=False)
    with pytest.raises(PrivacyViolation):
        router_with(tmp_path, [cloud], {"private": ["openrouter:any/model"]})


def test_build_router_uses_fast_model_for_research(tmp_home):
    settings = Settings(openrouter_model="big/model", openrouter_fast_model="small/model")
    router = build_router(settings, Budget(Store(tmp_home.data / "m.db"), 5.0), KillSwitch())
    assert "openrouter" in router.providers
    assert "openrouter:small/model" in router.routes["research"]
    assert "openrouter" in router.routes["plan"]
