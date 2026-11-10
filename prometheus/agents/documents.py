"""Reads documents on the laptop and summarises them with the local model only."""
from __future__ import annotations

import json
from pathlib import Path

from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, register, untrusted
from prometheus.agents.textutil import extract_text
from prometheus.safety.paths import PathBlocked

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {"relevant": {"type": "boolean"}, "summary": {"type": "string"}},
    "required": ["relevant", "summary"],
    "additionalProperties": False,
}


@register
class Documents(Agent):
    name = "documents"
    description = ("Reads PDF, DOCX and text files on this laptop and summarises the ones that match a question. "
                   "Inputs: paths (list of file paths, or '$stepN.pdfs'), question (what to look for).")
    private_outputs = True
    tools = {"read_files", "call_model"}

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        ctx.use(self.name, "call_model")
        question = str(inputs.get("question", "")).strip()
        summaries, problems = [], []
        paths = inputs.get("paths") or []
        if isinstance(paths, str):
            paths = [paths]
        for raw in paths:
            ctx.killswitch.check()
            try:
                path = ctx.policy.check_read(Path(raw))
            except PathBlocked:
                problems.append(f"{Path(raw).name}: not allowed")
                continue
            text = extract_text(path)
            if not text.strip():
                problems.append(f"{path.name}: no readable text")
                continue
            ask = (f"Question: {question or 'summarise it'}\n" + untrusted(text, path.name)
                   + "\nIs this document relevant to the question? Give a short summary. Answer in JSON.")
            # ProviderUnavailable propagates: the orchestrator pauses the task with its message.
            reply = ctx.router.complete("private", [{"role": "user", "content": ask}],
                                        "You read a person's documents privately. " + UNTRUSTED_RULE,
                                        json_schema=JUDGE_SCHEMA, max_tokens=400)
            try:
                verdict = json.loads(reply.text)
            except ValueError:
                problems.append(f"{path.name}: the model gave an unreadable answer")
                continue
            if verdict.get("relevant") or not question:
                summaries.append({"path": str(path), "summary": str(verdict.get("summary", ""))})

        lines = [f"- {Path(s['path']).name}: {s['summary']}" for s in summaries]
        summary = f"Read {len(paths)} files, {len(summaries)} relevant."
        if lines:
            summary += "\n" + "\n".join(lines)
        if problems:
            summary += "\nProblems: " + "; ".join(problems)
        ctx.store.add_agent_note(ctx.task_id, self.name, summary)
        return StepResult("done", summary, {"summaries": summaries})
