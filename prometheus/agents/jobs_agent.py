"""Ready-made jobs from the "Build your first AI agent" guide.

- actions: notes or a transcript (a call, a meeting, a lecture) → decisions, actions (who, what, when; unclear
  owners marked TBC) and a follow-up message under 150 words. Only what was said; no new promises. Never sent.
- digest: news on chosen topics from the last 7 days, at most 6 items, each with why it matters and its link.
  No link, no item. ("Every Monday at 8am give me a news digest on AI and cricket" makes it weekly.)
- sheet: several PDFs (bills, forms, marksheets) → one Excel sheet, a row per PDF, a column per field asked for.
  A field that isn't in the PDF is MISSING, one the AI isn't sure about is marked CHECK.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, register, untrusted
from prometheus.agents.textutil import extract_text
from prometheus.safety.paths import PathBlocked

MAX_PDFS = 40
ACTIONS_SCHEMA = {"type": "object", "properties": {
    "decisions": {"type": "array", "items": {"type": "string"}},
    "actions": {"type": "array", "items": {"type": "object", "properties": {
        "who": {"type": "string"}, "what": {"type": "string"}, "when": {"type": "string"}},
        "required": ["who", "what", "when"], "additionalProperties": False}},
    "follow_up": {"type": "string"}},
    "required": ["decisions", "actions", "follow_up"], "additionalProperties": False}
DIGEST_SCHEMA = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {
    "n": {"type": "integer"}, "why": {"type": "string"}}, "required": ["n", "why"], "additionalProperties": False}}},
    "required": ["items"], "additionalProperties": False}


def news_search(query: str, n: int) -> list[dict]:
    from ddgs import DDGS

    return [{"title": r.get("title", ""), "url": r.get("url", ""), "date": r.get("date", ""),
             "source": r.get("source", ""), "body": r.get("body", "")}
            for r in DDGS().news(query, timelimit="w", max_results=n)]


def _paths(raw) -> list[str]:
    if isinstance(raw, str):
        return [p for p in re.split(r"[\n;|]", raw) if p.strip()]
    return [str(p) for p in (raw or [])]


@register
class JobsAgent(Agent):
    name = "jobs"
    description = ("Ready-made jobs. action=actions: notes or a transcript (text, or path to a file) → decisions, "
                   "actions with who/what/when and a follow-up message draft (never sent). action=digest: topics → "
                   "this week's news, at most 6 items with links. action=sheet: paths (PDF files, or a folder) and "
                   "fields (what to pull out of each, e.g. 'date, amount, bill number') → one Excel sheet with MISSING "
                   "and CHECK marked. Inputs: action, text, path, topics, paths, fields, name.")
    tools = {"read_files", "move_files", "call_model", "web_search"}
    private_outputs = True
    required_inputs = ("action",)

    def __init__(self, search=None):
        self.search = search or news_search

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        action = str(inputs.get("action") or "").lower()
        if action == "actions":
            return self._actions(inputs, ctx)
        if action == "digest":
            return self._digest(goal, inputs, ctx)
        if action == "sheet":
            return self._sheet(inputs, ctx)
        return StepResult("failed", "I can do: actions (notes → to-do list and follow-up), digest (weekly news), "
                                    "sheet (PDFs → Excel).")

    # -- notes → actions and a follow-up ----------------------------------------------------------------
    def _actions(self, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "call_model")
        text = str(inputs.get("text") or "")
        if inputs.get("path"):
            ctx.use(self.name, "read_files")
            try:
                text = extract_text(ctx.policy.check_read(Path(str(inputs["path"])).expanduser()), limit=60_000)
            except PathBlocked as e:
                return StepResult("failed", f"I can't read that file: {e}")
        if len(text.strip()) < 20:
            return StepResult("failed", "Give me the notes or the transcript (paste it, or say which file).")
        reply = ctx.router.complete("private", [{"role": "user", "content": (
            "Turn these notes into: 1) decisions made, 2) actions, each with who, what and when (write TBC when it "
            "isn't said), 3) a short follow-up message (under 150 words) the owner could send. Only record what was "
            "said: add no new promises, names or dates.\n\n" + untrusted(text[:40_000], "notes"))}],
            "You turn meeting and lecture notes into clear actions. " + UNTRUSTED_RULE,
            json_schema=ACTIONS_SCHEMA, max_tokens=2000)
        try:
            data = json.loads(reply.text)
        except ValueError:
            return StepResult("failed", "I couldn't make sense of the notes. Try again with clearer notes.")
        lines = ["**Decisions**"] + ([f"- {d}" for d in data["decisions"]] or ["- none"])
        lines += ["", "**Actions**"] + ([f"- {a['what']} — {a['who'] or 'TBC'}, {a['when'] or 'TBC'}"
                                         for a in data["actions"]] or ["- none"])
        follow = " ".join(data.get("follow_up", "").split()[:150])
        lines += ["", "**Follow-up message (draft, not sent)**", follow]
        return StepResult("done", "\n".join(lines))

    # -- weekly news digest --------------------------------------------------------------------------------------
    def _digest(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "web_search")
        ctx.use(self.name, "call_model")
        topics = inputs.get("topics") or goal
        topics = [t.strip() for t in (topics if isinstance(topics, list) else re.split(r",| and ", str(topics)))
                  if t.strip()][:5]
        found: list[dict] = []
        for t in topics:
            try:
                found += [r for r in self.search(t, 8) if str(r.get("url", "")).startswith("https://")]
            except Exception:
                continue
        seen, news = set(), []
        for r in found:
            if r["url"] not in seen:
                seen.add(r["url"])
                news.append(r)
        if not news:
            return StepResult("failed", "I couldn't find news on that this week (or the search is down).")
        listing = "\n".join(f"[{i}] {r['title']} ({r.get('source', '')}, {r.get('date', '')[:10]}): "
                            f"{r.get('body', '')[:300]}" for i, r in enumerate(news[:30], 1))
        reply = ctx.router.complete("research", [{"role": "user", "content": (
            f"Topics: {', '.join(topics)}. Pick at most 6 of these news items that matter most to the owner, a "
            "student. For each give its number n and one line on why it matters.\n\n"
            + untrusted(listing, "news search results"))}],
            "You write a short weekly news digest. " + UNTRUSTED_RULE, json_schema=DIGEST_SCHEMA, max_tokens=900)
        try:
            picks = json.loads(reply.text)["items"]
        except (ValueError, KeyError):
            picks = [{"n": i, "why": ""} for i in range(1, min(6, len(news)) + 1)]
        lines = [f"**News digest: {', '.join(topics)}**"]
        for p in picks[:6]:
            i = int(p.get("n", 0))
            if 1 <= i <= len(news):             # no link, no item: only results the search really returned
                r = news[i - 1]
                lines.append(f"- **{r['title']}** — {p.get('why', '')} [{r.get('source') or 'link'}]({r['url']})")
        return StepResult("done", "\n".join(lines))

    # -- PDFs → spreadsheet -------------------------------------------------------------------------------------
    def _sheet(self, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        ctx.use(self.name, "call_model")
        fields = [f.strip() for f in re.split(r",|;|\n", str(inputs.get("fields") or "")) if f.strip()][:15]
        if not fields:
            return StepResult("failed", "Which details should I pull out of each PDF? For example: date, amount.")
        pdfs: list[Path] = []
        for raw in _paths(inputs.get("paths") or inputs.get("path")):
            try:
                p = ctx.policy.check_read(Path(raw.strip()).expanduser())
            except PathBlocked:
                continue
            if p.is_dir():
                pdfs += sorted(x for x in p.iterdir() if x.suffix.lower() == ".pdf")
            elif p.suffix.lower() == ".pdf":
                pdfs.append(p)
        pdfs = pdfs[:MAX_PDFS]
        if not pdfs:
            return StepResult("failed", "I didn't find any PDFs there. Say the folder or the files.")
        schema = {"type": "object", "properties": {f"f{i}": {"type": "object", "properties": {
            "value": {"type": "string"}, "sure": {"type": "boolean"}}, "required": ["value", "sure"],
            "additionalProperties": False} for i in range(len(fields))},
            "required": [f"f{i}" for i in range(len(fields))], "additionalProperties": False}
        asked = "\n".join(f"f{i}: {f}" for i, f in enumerate(fields))
        rows, missing = [], 0
        for pdf in pdfs:
            ctx.killswitch.check()
            text = extract_text(pdf, limit=20_000)
            cells = []
            if not text.strip():
                cells = ["MISSING (no text: a scan?)"] * len(fields)
            else:
                reply = ctx.router.complete("private", [{"role": "user", "content": (
                    f"From this document, give each field. If it isn't in the document, value = \"\". sure = false "
                    f"when you are guessing.\nFields:\n{asked}\n\n" + untrusted(text, pdf.name))}],
                    "You copy details out of documents exactly. " + UNTRUSTED_RULE, json_schema=schema,
                    max_tokens=800)
                try:
                    data = json.loads(reply.text)
                except ValueError:
                    data = {}
                for i in range(len(fields)):
                    v = data.get(f"f{i}", {})
                    value = " ".join(str(v.get("value", "")).split()).replace("|", "/")
                    cells.append("MISSING" if not value else (value if v.get("sure", True) else f"{value} (CHECK)"))
            missing += sum(c.startswith("MISSING") for c in cells)
            rows.append([pdf.name] + cells)
        table = "| File | " + " | ".join(fields) + " |\n|" + "---|" * (len(fields) + 1) + "\n"
        table += "\n".join("| " + " | ".join(r) + " |" for r in rows)
        name = re.sub(r"[^\w .-]", "", str(inputs.get("name") or "PDF details")).strip() or "PDF details"
        from prometheus.agents.files import Files

        made = Files()._create({"path": f"Documents/Prometheus sheets/{name}.xlsx", "content": table}, ctx)
        if made.status != "done":
            return made
        checks = sum(c.endswith("(CHECK)") for r in rows for c in r)
        made.summary = (f"Read {len(pdfs)} PDFs into {name}.xlsx (Documents/Prometheus sheets). "
                        f"{missing} cells MISSING, {checks} marked CHECK: look at those before you rely on the sheet.")
        return made
