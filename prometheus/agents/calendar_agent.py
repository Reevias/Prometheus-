"""Calendar: what's on today, tomorrow or this week, from the owner's private calendar link (read only)."""
from __future__ import annotations

from datetime import datetime, timedelta

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.config import get_secret
from prometheus.connectors.calendar_ics import CalendarError, day_range, describe, events_between, fetch

FETCH = fetch          # tests replace this


@register
class CalendarAgent(Agent):
    name = "calendar"
    description = ("The owner's calendar (read only). Actions: agenda (when = today, tomorrow, week or month), "
                   "search (query, within the next 3 months). Use it for 'what's on tomorrow?', 'am I free on "
                   "Friday?', 'when is the dentist?'.")
    tools = {"read_files"}
    private_outputs = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        links = [u.strip() for u in (get_secret("CALENDAR_LINKS") or "").split("|") if u.strip()]
        if not links:
            return StepResult("failed", "No calendar connected yet. Add your calendar's secret iCal link in Settings, "
                                        "under Connections.")
        events = []
        try:
            for url in links[:5]:
                events += FETCH(url)
        except CalendarError as e:
            return StepResult("failed", str(e))
        action = str(inputs.get("action") or "agenda").strip().lower()
        if action == "search":
            words = [w for w in str(inputs.get("query") or goal).lower().split() if len(w) > 2]
            start, end, _ = day_range("month")
            found = [e for e in events_between(events, start, start + timedelta(days=92))
                     if any(w in e["title"].lower() or w in e["location"].lower() for w in words)]
            label = "matching events in the next 3 months"
        else:
            start, end, label = day_range(str(inputs.get("when") or goal), datetime.now())
            found = events_between(events, start, end)
            label = f"events {label}"
        if not found:
            return StepResult("done", f"No {label}.")
        return StepResult("done", f"{len(found)} {label}:\n" + untrusted("\n".join(f"- {describe(e)}" for e in found[:40]),
                                                                       "calendar"))
