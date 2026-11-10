import httpx

from prometheus.catalog import load_catalog, model_order
from prometheus.models.base import ProviderUnavailable
from prometheus.models.compat import OpenAICompatProvider


def test_model_order_prefers_strong_chat_models():
    order = model_order(["text-embedding-3", "tiny-1b", "llama-3.3-70b-instruct", "whisper-large", "model-8b"])
    assert order[0] == "llama-3.3-70b-instruct" and "text-embedding-3" not in order and "whisper-large" not in order
    gemini = next(p for p in load_catalog() if p.id == "gemini")
    assert model_order(gemini.models, gemini.name)[0].startswith("gemini")


def test_every_supported_provider_gets_a_model():
    for p in load_catalog():
        if p.supported and p.models:
            assert OpenAICompatProvider(p, enabled=True).model, p.name


def test_retired_model_falls_through_to_the_next(tmp_home, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_1234")
    info = next(p for p in load_catalog() if p.id == "groq")
    asked = []

    def handler(request):
        import json
        model = json.loads(request.content)["model"]
        asked.append(model)
        if len(asked) == 1:
            return httpx.Response(404, json={"error": "model not found"})
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}})

    prov = OpenAICompatProvider(info, enabled=True, http=httpx.Client(transport=httpx.MockTransport(handler)))
    first = prov.model
    reply = prov.complete([{"role": "user", "content": "x"}], "sys")
    assert reply.text == "hi" and asked[0] == first and prov.model == asked[1] != first   # remembered


def test_bad_key_does_not_loop_through_models(tmp_home, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_1234")
    info = next(p for p in load_catalog() if p.id == "groq")
    calls = []
    prov = OpenAICompatProvider(info, enabled=True, http=httpx.Client(transport=httpx.MockTransport(
        lambda r: calls.append(1) or httpx.Response(401))))
    import pytest
    with pytest.raises(ProviderUnavailable, match="key was rejected"):
        prov.complete([{"role": "user", "content": "x"}], "sys")
    assert len(calls) == 1
