"""Looks things up on Wikipedia (free, no key) and answers with the article as the source."""
from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, register, untrusted

LANG = re.compile(r"^[a-z]{2,3}(-[a-z]{2,8})?$")
USER_AGENT = "Prometheus/1.4 (personal assistant)"
ARTICLE_CHARS = 200_000
SEND_CHARS = 5000


class WikipediaError(Exception):
    pass


def article_url(title: str, lang: str = "en") -> str:
    return f"https://{lang}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"


def wiki_articles(query: str, n: int, http: httpx.Client, lang: str = "en") -> list[tuple[str, str, str]]:
    """Top matching articles as (title, url, plain text). Only ever talks to <lang>.wikipedia.org."""
    if not LANG.match(lang):
        raise WikipediaError(f"'{lang}' is not a Wikipedia language code.")
    api = f"https://{lang}.wikipedia.org/w/api.php"
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    try:
        found = http.get(api, params={"action": "query", "list": "search", "srsearch": query[:300],
                                      "srlimit": n, "format": "json", "formatversion": 2},
                         headers=headers, timeout=15.0)
        found.raise_for_status()
        titles = [h["title"] for h in found.json().get("query", {}).get("search", [])][:n]
        if not titles:
            return []
        pages = http.get(api, params={"action": "query", "prop": "extracts", "explaintext": 1, "redirects": 1,
                                      "titles": "|".join(titles), "format": "json", "formatversion": 2},
                         headers=headers, timeout=20.0)
        pages.raise_for_status()
        by_title = {p.get("title"): p.get("extract", "") for p in pages.json().get("query", {}).get("pages", [])}
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (403, 429):
            raise WikipediaError("Wikipedia is limiting requests from this connection right now. "
                                 "Try again in a minute.") from e
        raise WikipediaError(f"Wikipedia answered with error {code}.") from e
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
        raise WikipediaError(f"Wikipedia could not be reached: {e}") from e
    out = []
    for t in titles:
        text = by_title.get(t, "")
        if text.strip():
            out.append((t, article_url(t, lang), text[:ARTICLE_CHARS]))
    return out


def best_part(ctx: AgentContext, title: str, url: str, text: str, question: str, budget: int) -> str:
    """Save the article as chunks and keep only the parts that answer the question."""
    doc_id = ctx.store.add_document(f"web:{url}", f"Wikipedia: {title}", url, text, private=False)
    return ctx.store.best_passages(doc_id, question, budget) if len(text) > budget else text


@register
class Wikipedia(Agent):
    name = "wikipedia"
    description = ("Looks up facts, people, places, history, science and definitions on Wikipedia and answers with "
                   "the article as the source. Free and reliable for encyclopedic facts. "
                   "Inputs: query, lang (Wikipedia language code, default en), max_articles (default 2).")
    tools = {"web_search", "web_fetch", "call_model"}
    accepts_private = False
    required_inputs = ("query",)

    def __init__(self, http: httpx.Client | None = None):
        self.http = http or httpx.Client(follow_redirects=False)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        for tool in ("web_search", "web_fetch", "call_model"):
            ctx.use(self.name, tool)
        query = str(inputs.get("query") or goal).strip()
        lang = str(inputs.get("lang") or "en").strip().lower()
        try:
            n = max(1, min(int(inputs.get("max_articles", 2)), 4))
        except (TypeError, ValueError):
            n = 2
        try:
            articles = wiki_articles(query, n, self.http, lang)
        except WikipediaError as e:
            return StepResult("failed", str(e))
        if not articles:
            return StepResult("failed", f"Wikipedia has no article matching: {query}")
        ctx.killswitch.check()
        blocks = "\n\n".join(f"[{i}] " + untrusted(best_part(ctx, t, u, text, query, SEND_CHARS), u)
                             for i, (t, u, text) in enumerate(articles, 1))
        prompt = (f"Question: {query}\n\nWikipedia articles:\n{blocks}\n\n"
                  "Answer the question using only these articles. Cite them as [1], [2]. "
                  "If they do not answer it, say so.")
        reply = ctx.router.complete("research", [{"role": "user", "content": prompt}],
                                    "You answer from Wikipedia carefully. " + UNTRUSTED_RULE + ctx.guidance(),
                                    max_tokens=1200)
        sources = [u for _, u, _ in articles]
        summary = reply.text.strip() + "\n\nSources:\n" + "\n".join(f"[{i}] {u}" for i, u in enumerate(sources, 1))
        ctx.store.add_agent_note(ctx.task_id, self.name, summary[:2000])
        return StepResult("done", summary, {"sources": sources})
