import json

import httpx

from prometheus.agents.research import Research
from prometheus.agents.wikipedia import Wikipedia, article_url, wiki_articles
from prometheus.models.fake import FakeProvider

LONG = "\n\n".join(f"Paragraph {i} about the city's markets and rivers." for i in range(400))
ARTICLE = "Paris is the capital of France.\n\n" + LONG + "\n\nThe Eiffel Tower was finished in 1889."


def wiki_transport(seen, articles=None):
    articles = {"Paris": ARTICLE} if articles is None else articles

    def handler(request):
        seen.append(request)
        q = dict(request.url.params)
        if q.get("list") == "search":
            return httpx.Response(200, json={"query": {"search": [{"title": t} for t in articles]}})
        titles = q["titles"].split("|")
        return httpx.Response(200, json={"query": {"pages": [{"title": t, "extract": articles[t]} for t in titles]}})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_only_talks_to_wikipedia_and_validates_language():
    seen = []
    out = wiki_articles("Paris", 2, wiki_transport(seen), "fr")
    assert out[0][0] == "Paris" and out[0][1] == "https://fr.wikipedia.org/wiki/Paris"
    assert {r.url.host for r in seen} == {"fr.wikipedia.org"}
    assert "Prometheus" in seen[0].headers["user-agent"]
    import pytest

    from prometheus.agents.wikipedia import WikipediaError
    with pytest.raises(WikipediaError):
        wiki_articles("x", 1, wiki_transport([]), "evil.com/")


def test_article_url_escapes_title():
    assert article_url("Rock & roll") == "https://en.wikipedia.org/wiki/Rock_%26_roll"


def test_agent_sends_relevant_part_and_cites(make_ctx):
    prompts = []

    def model(messages, system):
        prompts.append(messages[0]["content"])
        return "The Eiffel Tower was finished in 1889 [1]."

    ctx = make_ctx([FakeProvider(model, name="fake")], routes={"research": ["fake"], "private": ["fake"]})
    res = Wikipedia(http=wiki_transport([])).run("look up", {"query": "Eiffel Tower finished"}, ctx)
    assert res.status == "done" and "wikipedia.org/wiki/Paris" in res.summary
    assert "1889" in prompts[0] and len(prompts[0]) < 8000          # long article cut to the matching part
    assert ctx.store.search_chunks("Eiffel")                        # saved for later questions


def test_no_article_found(make_ctx):
    ctx = make_ctx([FakeProvider(["x"], name="fake")], routes={"research": ["fake"], "private": ["fake"]})
    res = Wikipedia(http=wiki_transport([], articles={})).run("q", {"query": "zzqqxx"}, ctx)
    assert res.status == "failed" and "no article" in res.summary.lower()


def test_research_falls_back_to_wikipedia_when_search_fails(make_ctx):
    def broken_search(q, n):
        raise RuntimeError("rate limited")

    ctx = make_ctx([FakeProvider(["Paris is the capital [1]."], name="fake")],
                   routes={"research": ["fake"], "private": ["fake"]})
    res = Research(search=broken_search, http=wiki_transport([])).run("q", {"query": "capital of France"}, ctx)
    assert res.status == "done" and res.data["sources"] == ["https://en.wikipedia.org/wiki/Paris"]


def test_planner_knows_the_agent():
    from prometheus.agents import REGISTRY

    assert "wikipedia" in REGISTRY and not REGISTRY["wikipedia"].accepts_private
    assert json.dumps(REGISTRY["wikipedia"].required_inputs) == '["query"]'


def test_rate_limit_gives_plain_message():
    import pytest

    from prometheus.agents.wikipedia import WikipediaError

    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(WikipediaError, match="Try again in a minute"):
        wiki_articles("Paris", 1, http)
