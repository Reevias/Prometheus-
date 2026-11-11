"""'Give me my briefing': the morning briefing or evening wrap-up, on demand."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register

SERVICES = None


@register
class Briefing(Agent):
    name = "briefing"
    description = ("The daily summary on demand: calendar, important email, weather, reminders, and what Prometheus "
                   "did. Inputs: kind = morning or evening.")
    tools = {"read_files"}
    private_outputs = True
    required_inputs = ("kind",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        from prometheus.briefing import build

        ctx.use(self.name, "read_files")
        kind = "evening" if "even" in str(inputs.get("kind") or "").lower() else "morning"
        if SERVICES is None:
            return StepResult("failed", "The briefing isn't set up.")
        return StepResult("done", build(SERVICES, kind))
