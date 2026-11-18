import json
from contextlib import contextmanager
from types import SimpleNamespace

from conftest import login
from prometheus.models.anthropic_provider import AnthropicProvider, FALLBACK_BETA


def test_claude_stream_shows_answer_and_accounts_for_thinking_retry(monkeypatch):
    calls, shown = [], []

    @contextmanager
    def stream(**kwargs):
        calls.append(kwargs)
        words = [] if len(calls) == 1 else ["Hello", " there"]
        response = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking="private"),
                                            SimpleNamespace(type="text", text="".join(words))],
                                   stop_reason="end_turn" if words else "max_tokens",
                                   usage=SimpleNamespace(input_tokens=20, output_tokens=10))
        yield SimpleNamespace(text_stream=iter(words), get_final_message=lambda: response)

    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(stream=stream)))
    p = AnthropicProvider(client=client)
    monkeypatch.setattr(p, "available", lambda: True)
    r = p.stream([{"role": "user", "content": "hi"}], "rules", shown.append, max_tokens=100)
    assert shown == ["Hello", " there"] and r.text == "Hello there"
    assert (r.tokens_in, r.tokens_out) == (40, 20)
    assert [c["max_tokens"] for c in calls] == [100, 16000]
    assert all(c["betas"] == [FALLBACK_BETA] and c["thinking"] == {"type": "adaptive"} for c in calls)


def test_sdk_stream_exposes_only_text(monkeypatch):
    import anthropic
    import httpx

    if not issubclass(anthropic.DefaultHttpxClient, httpx.Client):
        import httpx2 as httpx  # newer Anthropic SDKs use this transport

    model = "claude-haiku-5-5"
    message = {"id": "example", "type": "message", "role": "assistant", "model": model,
               "content": [], "stop_reason": None, "stop_sequence": None,
               "usage": {"input_tokens": 5, "output_tokens": 0}}
    events = [
        ("message_start", {"message": message}),
        ("content_block_start", {"index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}}),
        ("content_block_delta", {"index": 0, "delta": {"type": "thinking_delta", "thinking": "private"}}),
        ("content_block_stop", {"index": 0}),
        ("content_block_start", {"index": 1, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"index": 1, "delta": {"type": "text_delta", "text": "Hi"}}),
        ("content_block_stop", {"index": 1}),
        ("message_delta", {"delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 7}}),
        ("message_stop", {}),
    ]
    body = "".join(f"event: {kind}\ndata: {json.dumps({'type': kind, **data})}\n\n" for kind, data in events)
    def respond(request):
        assert json.loads(request.content)["stream"] is True
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    with httpx.Client(transport=httpx.MockTransport(respond)) as http:
        sdk = anthropic.Anthropic(api_key="offline-test", http_client=http)
        provider = AnthropicProvider(client=sdk, model=model)
        monkeypatch.setattr(provider, "available", lambda: True)
        shown = []
        reply = provider.stream([{"role": "user", "content": "hi"}], "rules", shown.append)
    assert shown == ["Hi"] and reply.text == "Hi" and (reply.tokens_in, reply.tokens_out) == (5, 7)


def test_assistant_state_uses_live_events(app_client):
    client, services = app_client()
    login(client)
    def state():
        return client.get("/api/v3/state").json()["assistant"]

    chat = services.store.create_chat("Test")
    assert state()["state"] == "idle"
    services.running[chat] = True
    o = services.orchestrator
    with o.activity(chat, "thinking", "Choosing a route"):
        assert state() == {"state": "thinking", "label": "Choosing a route"}
        with o.activity(chat, "acting", "Round 2"):
            assert state() == {"state": "acting", "label": "Round 2"}
    o.live_text[chat] = "private words"
    assert state() == {"state": "answering", "label": "Answering"}
    o.live_text.clear()
    services.running.clear()
    services.voice = SimpleNamespace(loop=None, error="", state=lambda: "listening")
    assert state()["state"] == "listening"
    services.voice = None
    item = services.approvals.request("test", "private summary", {})
    assert state()["state"] == "waiting_approval" and "private" not in json.dumps(state())
    services.killswitch.trigger("test")
    assert state()["state"] == "stopped"
    services.killswitch.reset()
    services.approvals.resolve(item, False)
    assert state()["state"] == "idle"
