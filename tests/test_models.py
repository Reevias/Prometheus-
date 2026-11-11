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


def test_an_empty_answer_moves_on_to_the_next_ai(tmp_path):
    empty = FakeProvider([""], name="thinker", is_local=False)
    good = FakeProvider(['{"steps": []}'], name="backup")
    r = make_router([empty, good], {"plan": ["thinker", "backup"]}, Budget(Store(tmp_path / "m.db"), cap_usd=5.0))
    assert r.complete("plan", [{"role": "user", "content": "x"}], "s").provider == "backup"


def test_only_empty_answers_say_so_plainly(tmp_path):
    empty = FakeProvider(["   "], name="thinker")
    r = make_router([empty], {"plan": ["thinker"]}, Budget(Store(tmp_path / "m.db"), cap_usd=5.0))
    with pytest.raises(ProviderUnavailable, match="empty answer.*another Brain"):
        r.complete("plan", [{"role": "user", "content": "x"}], "s")


def test_claude_gets_more_room_when_thinking_used_it_all(monkeypatch):
    from types import SimpleNamespace

    from prometheus.models.anthropic_provider import AnthropicProvider

    calls = []

    class Msgs:
        def create(self, **kw):
            calls.append(kw["max_tokens"])
            if len(calls) == 1:
                return SimpleNamespace(stop_reason="max_tokens", content=[SimpleNamespace(type="thinking", thinking="…")],
                                       usage=SimpleNamespace(input_tokens=10, output_tokens=4000))
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"steps": []}')],
                                   usage=SimpleNamespace(input_tokens=10, output_tokens=200))

    p = AnthropicProvider.__new__(AnthropicProvider)
    p.name, p.model, p.effort, p.is_local = "anthropic", "claude-opus-5-5", "high", False
    monkeypatch.setattr(p, "available", lambda: True, raising=False)
    monkeypatch.setattr(p, "_get_client", lambda: SimpleNamespace(beta=SimpleNamespace(messages=Msgs())), raising=False)
    reply = p.complete([{"role": "user", "content": "x"}], "s", json_schema={"type": "object"}, max_tokens=4000)
    assert reply.text == '{"steps": []}' and calls == [4000, 16000]


def test_new_models_ripple_codes_ollama_cloud_never_private(monkeypatch):
    from prometheus.catalog import load_catalog
    from prometheus.config import Settings, load_settings, save_settings
    from prometheus.models.router import build_routes, openrouter_models

    assert any(m["id"] == "ricursiveai/ripple-65b-preview" for m in openrouter_models())
    cloud = next(p for p in load_catalog() if p.id == "ollama-cloud")
    assert cloud.base_url == "https://ollama.com/v1" and cloud.key_name == "OLLAMA_CLOUD_API_KEY"
    assert "kimi-k3.1-pro" in cloud.models
    routes = build_routes(Settings())
    assert routes["code"][0] == "openrouter:ricursiveai/ripple-65b-preview"
    assert routes["code"][1:] == [e for e in routes["plan"] if e != routes["code"][0]]   # normal brain behind it
    assert not any(e.startswith("ollama-cloud") for e in routes.get("private", []))
    assert "code" not in build_routes(Settings(code_brain="off"))
    assert Settings().ollama_model == "granite4.2:3b" and Settings().embed_model == "embeddinggemma-2:270m"


def test_old_default_models_move_to_the_new_ones(tmp_path):
    from prometheus.config import Settings, load_settings, save_settings

    path = tmp_path / "settings.json"
    save_settings(Settings(ollama_model="qwen3.5:2b", embed_model="nomic-embed-text"), path)
    s = load_settings(path)
    assert s.ollama_model == "granite4.2:3b" and s.embed_model == "embeddinggemma-2:270m"
    save_settings(Settings(ollama_model="llama9:7b"), path)
    assert load_settings(path).ollama_model == "llama9:7b"          # one picked by hand stays


def test_coding_requests_go_to_the_coding_brain():
    from prometheus.orchestrator.planner import CODE_REQUEST

    assert CODE_REQUEST.search("fix this python error") and CODE_REQUEST.search("write a script to rename files")
    assert not CODE_REQUEST.search("what's the weather tomorrow") and not CODE_REQUEST.search("find my physics notes")


def test_paid_ai_waits_for_a_yes(tmp_path):
    from prometheus.models.base import PaidNeedsYes

    paid = FakeProvider(["from paid"], name="paidai", is_local=False)
    free = FakeProvider(["from free"], name="freeai")
    budget = Budget(Store(tmp_path / "m.db"), cap_usd=5.0)
    r = make_router([paid, free], {"plan": ["paidai", "freeai"]}, budget)
    r.paid_needs_yes = True
    msg = [{"role": "user", "content": "x"}]
    assert r.complete("plan", msg, "s").text == "from free"            # free first, nothing asked
    only_paid = make_router([paid], {"plan": ["paidai"]}, budget)
    only_paid.paid_needs_yes = True
    with pytest.raises(PaidNeedsYes, match="yes, use paid"):
        only_paid.complete("plan", msg, "s")
    with only_paid.paid_allowed():
        assert only_paid.complete("plan", msg, "s").text == "from paid"   # after the yes, this once
    with pytest.raises(PaidNeedsYes):
        only_paid.complete("plan", msg, "s")                             # and only that once
    only_paid.head = "paidai"
    assert only_paid.complete("plan", msg, "s").text == "from paid"       # a paid Brain picked by hand is a yes


def test_yes_use_paid_reruns_the_waiting_request(app_client):
    from prometheus.models.fake import FakeProvider as FP

    paid = FP(['{"steps": [], "answer": "Paid answer."}'], name="paidai", is_local=False)
    client, services = app_client(providers=[paid], routes={"plan": ["paidai"], "combine": ["paidai"],
                                                            "research": ["paidai"], "private": []})
    services.router.paid_needs_yes = True
    store = services.store
    chat = store.create_chat("t")
    first = services.orchestrator.run(chat, "explain black holes")
    assert first.status == "paused" and "yes, use paid" in first.answer
    again = services.orchestrator.run(chat, "yes, use paid")
    assert again.status == "done" and "Paid answer." in again.answer
    assert services.orchestrator.run(chat, "yes use paid").answer.startswith("There's nothing waiting")


def test_the_ask_first_switch(app_client):
    from tests.conftest import login

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    assert services.settings.paid_needs_yes and "Ask me before using a paid AI" in client.get("/settings").text
    client.post("/settings/paid-yes", data={"on": ""}, headers=h)
    assert services.settings.paid_needs_yes is False and services.router.paid_needs_yes is False


def test_plans_are_read_through_thinking_and_chatter():
    from prometheus.orchestrator.planner import json_part

    plan = '{"steps": [], "answer": "Hi {there}", "note": "a \\\\"quote\\\\" }"}'
    assert json_part("<think>let me plan {maybe}</think>\n" + plan) == plan
    assert json_part("Sure! Here is the plan:\n```json\n" + plan + "\n```\nHope that helps.") == plan
    assert json_part("Here you go: " + plan + " Anything else?") == plan


def test_an_ai_that_cant_plan_hands_over_to_the_next(app_client):
    from prometheus.models.fake import FakeProvider as FP

    bad = FP(["I would search your notes and then answer."], name="talker")
    good = FP(['{"steps": [], "answer": "Here you are."}'], name="planner2")
    client, services = app_client(providers=[bad, good], routes={"plan": ["talker", "planner2"], "combine": ["planner2"],
                                                                 "research": ["planner2"], "private": []})
    out = services.orchestrator.run(services.store.create_chat("t"), "translate this Chinese into English: 你好")
    assert out.status == "done" and "Here you are." in out.answer and len(bad.calls) == 2
