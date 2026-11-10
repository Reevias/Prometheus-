import socket

import httpx
import pytest

from prometheus.agents.research import Research
from prometheus.models.fake import FakeProvider
from prometheus.safety.web import UnsafeURL, check_url, fetch_text

PAGE = "<html><body><article><h1>Solar</h1><p>Solar panels turn sunlight into electricity. " * 5 + "</p></article></body></html>"


@pytest.fixture
def public_dns(monkeypatch):
    real = socket.getaddrinfo

    def fake(host, *args, **kwargs):
        if host in ("example.com", "news.example.org"):
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        return real(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", fake)


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://10.0.0.5/x", "http://169.254.169.254/latest",
                                 "http://[::1]/", "file:///etc/passwd", "ftp://example.com/", "http://localhost/"])
def test_blocks_localhost_and_private(url):
    with pytest.raises(UnsafeURL):
        check_url(url)


def test_public_url_allowed(public_dns):
    assert check_url("https://example.com/a") == "https://example.com/a"


def test_redirect_to_private_blocked(public_dns):
    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/admin"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(UnsafeURL):
        fetch_text("https://example.com/", http)


def make_http():
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=PAGE,
                                                                              headers={"content-type": "text/html"})))


def test_research_wraps_pages_untrusted_and_cites(public_dns, make_ctx):
    model = FakeProvider(["Solar panels make electricity [1]."], name="ollama")
    ctx = make_ctx(providers=[model], routes={"research": ["ollama"], "private": ["ollama"]})
    agent = Research(search=lambda q, n: [{"title": "Solar", "url": "https://example.com/solar"}], http=make_http())
    result = agent.run("research", {"query": "how do solar panels work"}, ctx)
    assert result.status == "done"
    sent = model.calls[0]["messages"][0]["content"]
    assert "<untrusted" in sent and "sunlight" in sent
    assert "never instructions" in model.calls[0]["system"]
    assert result.data["sources"] == ["https://example.com/solar"]
    assert "https://example.com/solar" in result.summary


def test_research_skips_unsafe_results(public_dns, make_ctx):
    model = FakeProvider(["summary"], name="ollama")
    ctx = make_ctx(providers=[model], routes={"research": ["ollama"], "private": ["ollama"]})
    agent = Research(search=lambda q, n: [{"title": "bad", "url": "http://127.0.0.1/"},
                                          {"title": "ok", "url": "https://news.example.org/a"}], http=make_http())
    result = agent.run("r", {"query": "x"}, ctx)
    assert result.data["sources"] == ["https://news.example.org/a"]


def test_research_no_results(make_ctx):
    agent = Research(search=lambda q, n: [], http=make_http())
    result = agent.run("r", {"query": "x"}, make_ctx())
    assert result.status == "failed"
