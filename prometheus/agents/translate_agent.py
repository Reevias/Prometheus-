"""Translates text, files and web pages between languages."""
from __future__ import annotations

import re
from pathlib import Path

import httpx

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.agents.textutil import extract_text
from prometheus.safety.paths import PathBlocked
from prometheus.translate import language, translate


@register
class TranslateAgent(Agent):
    name = "translate"
    description = ("Translates between languages (Hindi, Chinese, Japanese, French, Spanish, Arabic and many more). "
                   "Actions: text (text = what to translate, to = the language, default English), file (path = a PDF, "
                   "Word or text file; the translation is saved next to your documents as a new file), page (url = a "
                   "web page; saved as a file too). Inputs: action, text, path, url, to.")
    tools = {"read_files", "move_files", "call_model", "web_fetch"}
    private_outputs = True
    required_inputs = ("action",)

    def __init__(self, http: httpx.Client | None = None):
        self.http = http

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "call_model")
        action = str(inputs.get("action") or "text").lower()
        to = language(str(inputs.get("to") or ""), "English")
        if action == "text":
            text = str(inputs.get("text") or "").strip()
            if not text:
                return StepResult("failed", "What should I translate? Paste the text, or name a file or a web page.")
            out, cut = translate(ctx.router, text, to, private=True, cloud_if_needed=True)
            return StepResult("done", untrusted(out, f"translation into {to}")
                              + ("\n\n(The text was long: this is the first part.)" if cut else ""))
        if action == "file":
            ctx.use(self.name, "read_files")
            try:
                path = ctx.policy.check_read(Path(str(inputs.get("path") or "")).expanduser())
            except PathBlocked as e:
                return StepResult("failed", f"I can't read that file: {e}")
            text = extract_text(path, limit=40_000)
            if not text.strip():
                return StepResult("failed", f"I couldn't find any text in {path.name} (a scan or a picture?).")
            out, cut = translate(ctx.router, text, to, private=True)        # your file: only the laptop's AI reads it
            return self._save(ctx, f"{path.stem} ({to})", out, cut, f"{path.name}")
        if action == "page":
            ctx.use(self.name, "web_fetch")
            from prometheus.safety.web import UnsafeURL, check_url, fetch_text

            url = str(inputs.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            try:
                check_url(url)
                text = fetch_text(url, self.http or httpx.Client(headers={"User-Agent": "Prometheus/0.1 (personal assistant)"}))
            except (UnsafeURL, httpx.HTTPError) as e:
                return StepResult("failed", f"I couldn't open that page ({type(e).__name__}).")
            if not (text or "").strip():
                return StepResult("failed", "That page has no readable text.")
            out, cut = translate(ctx.router, text[:40_000], to, private=False)   # a public page: a free online AI may help
            host = re.sub(r"[^\w.-]", "", url.split("//", 1)[-1].split("/", 1)[0])[:40] or "page"
            return self._save(ctx, f"{host} ({to})", f"Source: {url}\n\n{out}", cut, url)
        return StepResult("failed", "I can translate: text, a file, or a web page.")

    def _save(self, ctx: AgentContext, name: str, text: str, cut: bool, source: str) -> StepResult:
        from prometheus.agents.files import Files

        name = re.sub(r"[^\w .()-]", "", name).strip() or "Translation"
        made = Files()._create({"path": f"Documents/Translations/{name}.md", "content": text}, ctx)
        if made.status != "done":
            return made
        made.summary = (f"Translated {source} and saved it as Documents/Translations/{name}.md."
                        + (" It was long, so this is the first part (about 12 pages)." if cut else "")
                        + "\n\nThe start of it:\n" + untrusted(text[:600], "translation"))
        return made
