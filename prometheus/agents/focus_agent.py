"""Focus mode on and off."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.focus import parse_length

FOCUS = None

@register
class FocusAgent(Agent):
    name = "focus"
    description = ("Focus mode: holds Prometheus's alerts, turns on Windows Do Not Disturb, closes the distracting "
                   "apps chosen in Settings and gives pomodoro break reminders. Actions: start (length = how long, "
                   "e.g. '2 hours', 'until 6pm', or empty for until stopped), stop, status.")
    tools = {"system_change"}
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "system_change")
        if FOCUS is None:
            return StepResult("failed", "Focus mode isn't set up.")
        action = str(inputs.get("action") or "").lower()
        if action == "start":
            if FOCUS.active():
                FOCUS.stop()
            return StepResult("done", FOCUS.start(parse_length(str(inputs.get("length") or ""), FOCUS.clock())))
        if action == "stop":
            return StepResult("done", FOCUS.stop())
        if action == "status":
            s = FOCUS.state()
            if not s:
                return StepResult("done", "Focus mode is off.")

            left = "until you say stop" if s["until"] is None else \
                f"{max(1, round((s['until'] - FOCUS.clock()) / 60))} minutes left"
            return StepResult("done", f"Focusing, {left}; {len(s['held'])} alert(s) held.")
        return StepResult("failed", "I can: start, stop, status.")
