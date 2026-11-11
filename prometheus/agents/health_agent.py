"""The laptop health check, on demand."""
from __future__ import annotations

from prometheus import health
from prometheus.agents.base import Agent, AgentContext, StepResult, register

SERVICES = None


@register
class HealthAgent(Agent):
    name = "health"
    description = ("Checks the laptop's health now: disk space, big old files, startup apps, Windows updates, "
                   "battery wear, backups, temporary files; offers a fix for each problem. No inputs needed.")
    tools = {"system_read"}
    private_outputs = True
    required_inputs = ()

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "system_read")
        if SERVICES is None:
            return StepResult("failed", "The health check isn't set up.")
        report = health.post(SERVICES, getattr(SERVICES, "laptop", None))
        return StepResult("done", report + "\n\nThe fixes are in the Laptop health chat (big ones on Approvals).")
