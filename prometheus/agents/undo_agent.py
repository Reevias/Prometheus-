"""'Undo that': reverses Prometheus's last action (within an hour), after a yes in the chat."""
from __future__ import annotations

from prometheus import undo
from prometheus.agents.base import Agent, AgentContext, StepResult, register


@register
class UndoAgent(Agent):
    name = "undo"
    description = ("Undoes the last thing Prometheus did in the past hour (files moved or made, a reminder or goal, "
                   "a calendar event, saved facts, a model change), after the owner says yes. Input: action = undo.")
    tools = {"move_files"}
    required_inputs = ()

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "move_files")
        if undo.SERVICES is None:
            return StepResult("failed", "Undo isn't set up.")
        item = undo.last(undo.SERVICES)
        if item is None:
            return StepResult("done", "There's nothing from the last hour that I can undo.")
        if item["kind"] == "email":
            return StepResult("done", "The last thing I did was send an email, and email can't be unsent.")
        return ctx.ask("undo", f"Undo: {item['summary']}", item, exact=f"undo:{item['source']}:{item['id']}",
                       waiting=f"Undo this: {item['summary']}? Answer below.")
