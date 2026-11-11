"""Runs Python for sums, data and charts in the sandbox (prometheus/sandbox.py). Each new script is shown in the
chat and run only after a yes; the internet is off unless the owner allows it for that run."""
from __future__ import annotations

import hashlib
import time
from pathlib import Path

from prometheus import sandbox
from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.safety.paths import PathBlocked

SERVICES = None        # the app's services (store, approvals, settings)
MAX_CODE = 20_000


@register
class CodeAgent(Agent):
    name = "code"
    description = ("Runs a Python script safely (no internet, only its own folder) for sums, statistics, data from a "
                   "CSV or Excel file, charts (matplotlib saves .png) and converting files. Inputs: code (the whole "
                   "script; print the answer; save files in the current folder), files (optional list of the "
                   "owner's files to copy in). Files it saves show in Outputs.")
    tools = {"run_code"}
    accepts_private = True          # the owner's data stays on the laptop
    required_inputs = ("code",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "run_code")
        code = str(inputs.get("code") or "").strip()
        if code.startswith("```"):
            code = code.strip("`").removeprefix("python").strip()
        if not code:
            return StepResult("failed", "There's no script to run.")
        if len(code) > MAX_CODE:
            return StepResult("failed", "That script is too long for me to run safely.")
        raw = inputs.get("files") or []
        files = []
        for f in [raw] if isinstance(raw, str) else raw:
            try:
                p = ctx.policy.check_read(Path(str(f)).expanduser())
            except PathBlocked as e:
                return StepResult("failed", f"I can't use {f}: {e}")
            if not p.is_file():
                return StepResult("failed", f"There's no file at {f}.")
            files.append(str(p))
        digest = hashlib.sha256(code.encode()).hexdigest()[:16]
        shown = code if len(code) < 1500 else code[:1500] + "\n# ... (more)"
        payload = {"code": code, "files": files, "task_id": ctx.task_id, "chat_id": ctx.chat_id(), "network": False}
        return ctx.ask("code_run", f"Run this Python script (no internet, only its own folder):\n```python\n{shown}\n```",
                       payload, exact=f"code:{digest}", broad="code:any",
                       broad_label="Always run scripts without asking",
                       waiting="I'd like to run a short Python script. It's shown below: allow it?")


def run_handler(payload: dict) -> str:
    """Runs after a yes (or straight away if scripts are always allowed)."""
    s = SERVICES
    use_ws = bool(getattr(s.settings, "code_windows_sandbox", True))
    from prometheus.config import data_dir

    result, folder = sandbox.run(payload["code"], data_dir() / "scripts", network=bool(payload.get("network")),
                                 use_windows_sandbox=use_ws, files=[Path(f) for f in payload.get("files", [])])
    for f in result.files:
        try:
            s.store.add_output(int(payload.get("task_id") or 0), str(f), "created")
        except Exception:
            pass
    text = result.describe()
    if result.needs_network:
        again = dict(payload, network=True)
        s.approvals.ask("code_network", "Let this script use the internet for one run", again,
                        (f"net:{time.time()}", ""), chat_id=int(payload.get("chat_id") or 0))
        text += "\nIt needs the internet. Allow it for one run below in the chat?"
    return text
