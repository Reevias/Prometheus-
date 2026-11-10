import json
from types import SimpleNamespace

import httpx
import pytest

from prometheus.memory.store import Store
from prometheus.models.anthropic_provider import AnthropicProvider
from prometheus.models.base import PrivacyViolation, ProviderUnavailable
from prometheus.models.fake import FakeProvider
from prometheus.models.ollama import OllamaProvider
from prometheus.models.router import ModelRouter
from prometheus.safety.budget import Budget, BudgetExceeded
from prometheus.safety.killswitch import KillSwitch, Stopped

NO_MODEL = "No AI model is available for 'plan'. Start Ollama or add an API key in Settings."


class Off(FakeProvider):
    def available(self):
        return False


@pytest.fixture
def budget(tmp_path):
    return Budget(Store(tmp_path / "m.db"), cap_usd=1.0)


def make_router(providers, routes, budget, ks=None):
    return ModelRouter({p.name: p for p in providers}, routes, budget, ks or KillSwitch())


def test_private_route_rejects_cloud(budget):
    cloud = FakeProvider(["x"], name="cloudfake", is_local=False)
    with pytest.raises(PrivacyViolation):
        make_router([cloud], {"private": ["cloudfake"]}, budget)


def test_router_falls_back_when_unavailable(budget):
    off = Off(["never"], name="anthropic", is_local=False)
    local = FakeProvider(["hello"], name="ollama")
    router = make_router([off, local], {"plan": ["anthropic", "ollama"]}, budget)
    reply = router.complete("plan", [{"role": "user", "content": "hi"}], "sys")
    assert reply.text == "hello" and reply.provider == "ollama"
    assert off.calls == []


def test_no_provider_message(budget):
    router = make_router([Off(["x"], name="ollama")], {"plan": ["ollama"]}, budget)
    with pytest.raises(ProviderUnavailable) as err:
        router.complete("plan", [{"role": "user", "content": "hi"}], "sys")
    assert str(err.value) == NO_MODEL


def test_cloud_call_records_cost_and_respects_budget(budget):
    cloud = FakeProvider(["ok"], name="anthropic", is_local=False, usd=0.6)
    router = make_router([cloud], {"plan": ["anthropic"]}, budget)
    router.complete("plan", [{"role": "user", "content": "a"}], "s")
    assert budget.state().spent == pytest.approx(0.6)
    router.complete("plan", [{"role": "user", "content": "b"}], "s")
    with pytest.raises(BudgetExceeded):
        router.complete("plan", [{"role": "user", "content": "c"}], "s")
    assert len(cloud.calls) == 2


def test_killswitch_blocks_calls(budget):
    ks = KillSwitch()
    local = FakeProvider(["x"], name="ollama")
    router = make_router([local], {"plan": ["ollama"]}, budget, ks)
    ks.trigger("stop")
    with pytest.raises(Stopped):
        router.complete("plan", [{"role": "user", "content": "a"}], "s")
    assert local.calls == []


class FakeAnthropicClient:
    def __init__(self, text="planned", stop_reason="end_turn"):
        self.kwargs = None
        self._text, self._stop = text, stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=self._text)],
            stop_reason=self._stop,
            usage=SimpleNamespace(input_tokens=1000, output_tokens=500),
            model="claude-opus-5-5",
        )


def test_anthropic_request_shape(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    client = FakeAnthropicClient()
    provider = AnthropicProvider(client=client)
    schema = {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"],
              "additionalProperties": False}
    reply = provider.complete([{"role": "user", "content": "hi"}], "sys", json_schema=schema)
    kw = client.kwargs
    assert kw["model"] == "claude-opus-5-5"
    assert kw["thinking"] == {"type": "adaptive"}
    assert kw["fallbacks"] == "default"
    assert "server-side-fallback-2026-07-01" in kw["betas"]
    assert kw["output_config"]["format"] == {"type": "json_schema", "schema": schema}
    assert reply.text == "planned"
    assert reply.usd == pytest.approx(0.014)
    assert provider.is_local is False


def test_anthropic_refusal_raises(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    provider = AnthropicProvider(client=FakeAnthropicClient(stop_reason="refusal"))
    with pytest.raises(ProviderUnavailable, match="refused"):
        provider.complete([{"role": "user", "content": "hi"}], "sys")


def test_anthropic_unavailable_without_key(tmp_home, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert AnthropicProvider(client=FakeAnthropicClient()).available() is False


def test_ollama_request_shape():
    seen = {}

    def handler(request):
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": []})
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": '{"a": "b"}'}, "prompt_eval_count": 7,
                                         "eval_count": 3})

    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://127.0.0.1:11434")
    provider = OllamaProvider("qwen3.5:2b", "http://127.0.0.1:11434", http=http)
    assert provider.available()
    schema = {"type": "object"}
    reply = provider.complete([{"role": "user", "content": "hi"}], "sys", json_schema=schema)
    assert seen["path"] == "/api/chat"
    assert seen["body"]["format"] == schema
    assert seen["body"]["stream"] is False
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert reply.text == '{"a": "b"}' and reply.usd == 0 and reply.tokens_in == 7


def test_ollama_unavailable_when_not_running():
    def handler(request):
        raise httpx.ConnectError("refused")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert OllamaProvider("m", "http://127.0.0.1:11434", http=http).available() is False
