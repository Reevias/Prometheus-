"""M3.5: answers word by word."""
from __future__ import annotations

import json

import httpx
from conftest import login

from prometheus.models.base import ProviderUnavailable, Reply
from prometheus.models.fake import FakeProvider
from prometheus.models.streaming import ollama_lines, openai_sse
from prometheus.orchestrator.planner import Orchestrator
from test_orchestrator import PROMPTS, REGISTRY


def sse(chunks):
    body = "".join(f"data: {json.dumps({'choices': [{'delta': {'content': c}}]})}\n\n" for c in chunks)
    body += "data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 3}}) + "\n\ndata: [DONE]\n\n"
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=body.encode())))


def test_openai_style_stream_is_read_piece_by_piece():
    got = []
    text, usage, _ = openai_sse(sse(["Hel", "lo", " there"]), "https://x/chat/completions", {"model": "m"}, {},
                                got.append, "X")
    assert got == ["Hel", "lo", " there"] and text == "Hello there" and usage["completion_tokens"] == 3


def test_ollama_stream_is_read_piece_by_piece():
    lines = "\n".join(json.dumps(x) for x in [{"message": {"content": "Hi"}}, {"message": {"content": "!"}},
                                               {"done": True, "eval_count": 2}])
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=lines.encode())))
    got = []
    text, last = ollama_lines(http, "http://127.0.0.1:11434/api/chat", {"model": "m"}, got.append)
    assert got == ["Hi", "!"] and text == "Hi!" and last["eval_count"] == 2


class Streamer(FakeProvider):
    def __init__(self, pieces, name, fail_after=None):
        super().__init__(["".join(pieces)], name=name)
        self.pieces, self.fail_after = pieces, fail_after

    def stream(self, messages, system, on_text, max_tokens=4096, model=None):
        for i, p in enumerate(self.pieces):
            if self.fail_after is not None and i == self.fail_after:
                raise ProviderUnavailable("dropped")
            on_text(p)
        return Reply(text="".join(self.pieces), provider=self.name, model="m")


def test_router_streams_and_starts_again_when_an_ai_fails_part_way(make_ctx):
    broken = Streamer(["Half", " an"], "broken", fail_after=1)
    good = Streamer(["Whole", " answer"], "good")
    ctx = make_ctx(providers=[broken, good], routes={"combine": ["broken", "good"]})
    shown = []
    reply = ctx.router.stream("combine", [{"role": "user", "content": "q"}], "s", on_text=shown.append)
    assert reply.text == "Whole answer"
    assert shown == [None, "Half", None, "Whole", " answer"]          # None = start again


def test_an_ai_that_cant_stream_gives_the_whole_answer(make_ctx):
    ctx = make_ctx(providers=[FakeProvider(["All at once."], name="plain")], routes={"combine": ["plain"]})
    shown = []
    assert ctx.router.stream("combine", [{"role": "user", "content": "q"}], "s", on_text=shown.append).text == "All at once."


def test_the_chat_can_watch_an_answer_being_written(make_ctx):
    seen = []

    class Watcher(Streamer):
        def stream(self, messages, system, on_text, max_tokens=4096, model=None):
            on_text("Plants ")
            seen.append(dict(o.live_text))
            on_text("make food.")
            return Reply(text="Plants make food.", provider=self.name, model="m")
    model = Watcher(["x"], "ai")
    ctx = make_ctx(providers=[model], routes={"plan": ["ai"], "combine": ["ai"], "research": ["ai"], "private": ["ai"]})
    o = Orchestrator(ctx.store, ctx.router, REGISTRY, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    o.front_door = True
    chat = ctx.store.create_chat("t")
    out = o.run(chat, "what is photosynthesis?")
    assert out.answer == "Plants make food." and seen == [{chat: "Plants "}] and o.live_text == {}


def test_live_text_endpoint(app_client):
    client, services = app_client()
    login(client)
    cid = services.store.create_chat("t")
    services.orchestrator.live_text[cid] = 'So far <untrusted source="x">\nwords'
    r = client.get(f"/api/v3/chats/{cid}/live-text").json()
    assert r["text"] == "So far words" and r["busy"] is False
