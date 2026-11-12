"""Finds what the owner copied or screenshotted in the last week."""
from __future__ import annotations

import time

from prometheus import clipboard
from prometheus.agents.base import Agent, AgentContext, StepResult, register


@register
class ClipboardAgent(Agent):
    name = "clipboard"
    description = ("What the owner copied (text and links) or screenshotted in the last 7 days. Inputs: query (words "
                   "to look for, optional), kind (link, text or screenshot, optional), when (today, this morning, "
                   "yesterday, last hour, optional).")
    tools = {"read_files"}
    private_outputs = True
    required_inputs = ()

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        mem = clipboard.CURRENT
        if mem is None:
            return StepResult("failed", "Clipboard memory isn't on.")
        kind = str(inputs.get("kind") or "").lower().rstrip("s")
        kind = kind if kind in ("link", "text", "screenshot") else ""
        since = clipboard.when_from(str(inputs.get("when") or goal), mem.clock())
        rows = mem.find(str(inputs.get("query") or ""), kind, since)
        if not rows:
            return StepResult("done", "I don't have anything like that from your clipboard or screenshots.")
        lines = []
        for r in rows:
            when = time.strftime("%a %H:%M", time.localtime(r["created"]))
            text = " ".join(r["text"].split())
            lines.append(f"- {when} ({r['kind']}): {text[:300]}" + (f" [{r['path']}]" if r["path"] else ""))
            if r["path"]:
                ctx.store.add_output(ctx.task_id, r["path"], "found")
        return StepResult("done", "\n".join(lines))
