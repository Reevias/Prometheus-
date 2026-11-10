"""Searches the web, reads a few pages and writes a sourced summary."""
from __future__ import annotations

from typing import Callable

import httpx

from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, register, untrusted
from prometheus.safety.web import UnsafeURL, check_url, fetch_text

PAGE_CHARS = 6000


def ddg_search(query: str, n: int) -> list[dict]:
    from ddgs import DDGS

    return [{"title": r.get("title", ""), "url": r.get("href", "")} for r in DDGS().text(query, max_results=n)]


@register
class Research(Agent):
    name = "research"
    description = "Searches the web and summarises what it finds, with sources. Inputs: query, max_sources (default 3)."
    tools = {"web_search", "web_fetch", "call_model"}
    accepts_private = False

    def __init__(self, search: Callable[[str, int], list[dict]] | None = None, http: httpx.Client | None = None):
        self.search = search or ddg_search
        self.http = http or httpx.Client(headers={"User-Agent": "Prometheus/0.1 (personal assistant)"})

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        for tool in ("web_search", "web_fetch", "call_model"):
            ctx.use(self.name, tool)
        query = str(inputs.get("query") or goal)
        max_sources = max(1, min(int(inputs.get("max_sources", 3)), 6))
        try:
            results = self.search(query, max_sources * 2)
        except Exception as e:
            return StepResult("failed", f"Web search failed: {e}")
        pages = []
        for r in results:
            if len(pages) >= max_sources:
                break
            ctx.killswitch.check()
            url = r.get("url", "")
            try:
                check_url(url)
                text = fetch_text(url, self.http)
            except (UnsafeURL, httpx.HTTPError):
                continue
            if text:
                pages.append((url, text[:PAGE_CHARS]))
        if not pages:
            return StepResult("failed", f"No readable web pages found for: {query}")
        blocks = "\n\n".join(f"[{i}] " + untrusted(text, url) for i, (url, text) in enumerate(pages, 1))
        prompt = (f"Question: {query}\n\nSources:\n{blocks}\n\n"
                  "Answer the question using only these sources. Cite them as [1], [2].")
        reply = ctx.router.complete("research", [{"role": "user", "content": prompt}],
                                    "You are a careful researcher. " + UNTRUSTED_RULE + ctx.guidance(),
                                    max_tokens=1500)
        sources = [url for url, _ in pages]
        summary = reply.text.strip() + "\n\nSources:\n" + "\n".join(f"[{i}] {u}" for i, u in enumerate(sources, 1))
        ctx.store.add_agent_note(ctx.task_id, self.name, summary[:2000])
        return StepResult("done", summary, {"sources": sources})
