"""Other apps connected through MCP (Notion, Slack, Spotify, home devices…): call one of their tools."""
from __future__ import annotations

import json

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.connectors.mcp_client import MCPError

MANAGER = None          # set by the app (one ConnectionManager for everything)


@register
class AppsAgent(Agent):
    name = "apps"
    description = ("Other apps the owner connected (listed under 'Connected apps' when there are any). Actions: "
                   "tools (app = which app: lists what it can do), call (app, tool, arguments = JSON object). Tools "
                   "that change something are asked in the chat first.")
    tools = {"read_files"}
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        if MANAGER is None:
            return StepResult("failed", "Connections aren't set up.")
        action = str(inputs.get("action") or "").strip().lower()
        app = str(inputs.get("app") or inputs.get("server") or "").strip()
        try:
            conn = MANAGER.get(app)
            if action == "tools":
                return StepResult("done", f"{conn.name} can:\n" + "\n".join(
                    f"- {t.get('name')}: {' '.join(str(t.get('description') or '').split())[:160]}" for t in conn.tools))
            if action != "call":
                return StepResult("failed", "I can: tools (what an app can do) or call (use one of its tools).")
            tool = str(inputs.get("tool") or "").strip()
            raw = inputs.get("arguments") or {}
            try:
                arguments = raw if isinstance(raw, dict) else json.loads(str(raw) or "{}")
            except ValueError:
                return StepResult("failed", "The tool's arguments must be a JSON object.")
            payload = {"app": conn.name, "tool": tool, "arguments": arguments}
            if conn.read_only(tool):
                return StepResult("done", untrusted(conn.call(tool, arguments), f"{conn.name}/{tool}"))
            shown = json.dumps(arguments)[:200]
            return ctx.ask("mcp_call", f"Let {conn.name} do '{tool}' with {shown}", payload,
                           exact=f"{conn.name}:{tool}", broad=f"{conn.name}:*",
                           broad_label=f"Any change through {conn.name}",
                           waiting=f"{conn.name} wants to '{tool}'. Allow it below in the chat?")
        except MCPError as e:
            return StepResult("failed", str(e))


def mcp_call_handler(payload: dict) -> str:
    try:
        conn = MANAGER.get(str(payload.get("app", "")))
        return untrusted(conn.call(str(payload.get("tool", "")), payload.get("arguments") or {}),
                         f"{payload.get('app')}/{payload.get('tool')}")
    except MCPError as e:
        raise RuntimeError(str(e)) from e
