"""Answers "how do I…" questions from a skill folder, reading only the reference files it needs."""
from __future__ import annotations

import json

from prometheus.agents.base import Agent, AgentContext, StepResult, register

PICK_SCHEMA = {"type": "object", "properties": {"files": {"type": "array", "items": {"type": "string"}}},
               "required": ["files"], "additionalProperties": False}
MAX_FILES = 2


@register
class Guide(Agent):
    name = "guide"
    description = ("Explains how to do something using one of Prometheus's skills (its how-to guides for research, "
                   "coding tools, understanding software, explaining code, memory, models, file sorting, video). "
                   "Inputs: skill (a skill name), question.")
    tools = {"call_model"}
    accepts_private = False

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "call_model")
        book = ctx.skills
        name = str(inputs.get("skill") or "").strip()
        question = str(inputs.get("question") or goal).strip()
        skill = book.get(name) if book else None
        if skill is None:
            known = ", ".join(s.name for s in book.catalog()) if book else "none"
            return StepResult("failed", f"There is no skill called '{name}'. Available skills: {known}.")
        refs = skill.references()
        chosen: list[str] = []
        if refs:
            ask = (f"Question: {question}\n\nSkill guide:\n{skill.body}\n\nReference files: {', '.join(refs)}\n\n"
                   f"Which reference files (at most {MAX_FILES}) are needed to answer? Reply as JSON.")
            reply = ctx.router.complete("plan", [{"role": "user", "content": ask}],
                                        "You pick which reference files to read. Choose as few as possible.",
                                        json_schema=PICK_SCHEMA, max_tokens=200)
            try:
                wanted = json.loads(reply.text).get("files", [])
            except (ValueError, AttributeError):
                wanted = []
            chosen = [f for f in wanted if isinstance(f, str) and f in refs][:MAX_FILES]
        material = "\n\n".join(f"## {ref}\n{book.reference(name, ref)}" for ref in chosen)
        prompt = f"Question: {question}\n\nSkill guide ({name}):\n{skill.body}\n\n{material}".strip()
        answer = ctx.router.complete(
            "plan", [{"role": "user", "content": prompt}],
            "You are Prometheus. Answer the question for a beginner using only the guide and references given. "
            "Give numbered steps where it helps. If the guide doesn't cover it, say so.", max_tokens=1500)
        return StepResult("done", answer.text.strip(), {"skill": name, "read": chosen})
