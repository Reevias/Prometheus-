"""Lets Prometheus make small tools for itself and use them again later (self-edit level 2)."""
from __future__ import annotations

import json
from pathlib import Path

from prometheus import sandbox
from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.own_tools import OwnTools, ToolError, digest
from prometheus.safety.paths import PathBlocked

TOOLS: OwnTools | None = None     # set by the app
SERVICES = None


@register
class ToolsAgent(Agent):
    name = "tools"
    description = ("Prometheus's own small tools (Python scripts it wrote, approved by the owner, run in the safe "
                   "sandbox). Actions: run (name, inputs = a JSON object for the tool, files = owner's files to "
                   "copy in), make (name like convert_heic, description, inputs = list of input names, code = a "
                   "complete Python script that reads json.load(open('inputs.json')), prints its answer and saves "
                   "files in the current folder; no internet), list. Make a tool only for a job that will come "
                   "back (converting, cleaning, calculating); for a one-off use the code agent.")
    tools = {"run_code"}
    accepts_private = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "run_code")
        if TOOLS is None:
            return StepResult("failed", "Own tools aren't set up.")
        action = str(inputs.get("action") or "").strip().lower()
        name = str(inputs.get("name") or "").strip().lower()
        try:
            if action == "list":
                tools = TOOLS.all()
                if not tools:
                    return StepResult("done", "I haven't made any tools yet.")
                return StepResult("done", "\n".join(
                    f"- {t['name']}: {t['description']}" + ("" if t["approved"] else " (waiting for your approval)")
                    for t in tools))
            if action == "make":
                raw = inputs.get("inputs") or []
                names = [raw] if isinstance(raw, str) else list(raw)
                meta = TOOLS.save_draft(name, str(inputs.get("description") or goal), str(inputs.get("code") or ""),
                                        names)
                tool = TOOLS.get(name)
                ctx.approvals.request("approve_tool", f"Let Prometheus keep and use its new tool '{name}': "
                                      f"{meta['description']}. It runs in the sandbox (no internet, only its own "
                                      "folder). Approve once; it asks again only if its code changes.",
                                      {"name": name, "sha": digest(tool["code"]), "diff": tool["code"]})
                return StepResult("waiting_approval", f"I wrote a tool called '{name}'. Its code is on the Approvals "
                                                      "page: approve it once and I can use it from then on.")
            if action == "run":
                tool = TOOLS.get(name)
                if tool is None:
                    return StepResult("failed", f"There's no tool called '{name}'.")
                if not tool["approved"]:
                    return StepResult("failed", f"The tool '{name}' isn't approved yet (or its code changed): "
                                                "approve it on the Approvals page first.")
                files = []
                raw = inputs.get("files") or []
                for f in [raw] if isinstance(raw, str) else raw:
                    try:
                        files.append(ctx.policy.check_read(Path(str(f)).expanduser()))
                    except PathBlocked as e:
                        return StepResult("failed", f"I can't use {f}: {e}")
                args = inputs.get("inputs") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {"text": args}
                from prometheus.config import data_dir

                use_ws = bool(getattr(getattr(SERVICES, "settings", None), "code_windows_sandbox", True))
                result, _ = sandbox.run(tool["code"], data_dir() / "scripts", network=False,
                                        use_windows_sandbox=use_ws, files=files,
                                        extra={"inputs.json": json.dumps(args)})
                TOOLS.count_run(name)
                for f in result.files:
                    ctx.store.add_output(ctx.task_id, str(f), "created")
                return StepResult("done" if result.ok else "failed", f"Tool '{name}': " + result.describe(),
                                  {"made": [str(f) for f in result.files]})
        except ToolError as e:
            return StepResult("failed", str(e))
        return StepResult("failed", "I can: run, make, list.")


def approve_handler(payload: dict) -> str:
    if TOOLS is None or not TOOLS.approve(str(payload.get("name", "")), str(payload.get("sha", ""))):
        return "That tool changed since you saw it, so it wasn't approved. I'll ask again with the new code."
    return f"Approved. I can use '{payload['name']}' from now on."
