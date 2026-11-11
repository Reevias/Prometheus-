"""Google Calendar (see, add and move events) and Google Drive (search and read), with the owner's sign-in."""
from __future__ import annotations

import time

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.connectors.google import Google, GoogleError
from prometheus.reminders import WhenError, describe, parse_when

CLIENT = None          # tests put a fake here


def google() -> Google:
    return CLIENT if CLIENT is not None else Google()


@register
class GoogleAgent(Agent):
    name = "google"
    description = ("Google Calendar and Drive. Actions: events (when = today, tomorrow, week; or query to find one), "
                   "add_event (title, when e.g. 'Friday at 5pm', minutes, location), move_event (id, when), "
                   "drive_search (query), drive_read (id). Adding or moving an event is asked in the chat first.")
    tools = {"read_files"}
    private_outputs = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        action = str(inputs.get("action") or "").strip().lower()
        try:
            g = google()
            if action == "events":
                word = str(inputs.get("when") or "week").lower()
                start = time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))
                if "tomorrow" in word:
                    start += 86400
                span = 86400 if ("today" in word or "tomorrow" in word) else 7 * 86400
                found = g.events(start, start + (90 * 86400 if inputs.get("query") else span),
                                 str(inputs.get("query") or ""))
                if not found:
                    return StepResult("done", "No events found.")
                lines = [f"- [{e['id']}] {e['start'][:16].replace('T', ' ')}: {e['title']}"
                         + (f" ({e['location']})" if e["location"] else "") for e in found]
                return StepResult("done", untrusted("\n".join(lines), "Google Calendar"), {"ids": [e["id"] for e in found]})
            if action in ("add_event", "move_event"):
                try:
                    when = parse_when(str(inputs.get("when") or ""))
                except WhenError as e:
                    return StepResult("failed", str(e))
                try:
                    minutes = max(5, min(int(inputs.get("minutes") or 60), 24 * 60))
                except ValueError:
                    minutes = 60
                title = " ".join(str(inputs.get("title") or "").split())[:200]
                if action == "add_event" and not title:
                    return StepResult("failed", "What should the event be called?")
                payload = {"action": action, "title": title, "start": when.due, "minutes": minutes,
                           "location": str(inputs.get("location") or "")[:200], "id": str(inputs.get("id") or "")}
                what = f"Add '{title}' to your calendar {describe(when.due)}" if action == "add_event" else \
                    f"Move the event to {describe(when.due)}"
                return ctx.ask("google_event", what, payload, exact=f"{action}:{title}:{int(when.due)}",
                               waiting=f"{what}? Answer below in the chat.")
            if action == "drive_search":
                files = g.drive_search(str(inputs.get("query") or goal))
                if not files:
                    return StepResult("done", "No Drive files match.")
                return StepResult("done", untrusted("\n".join(f"- [{f['id']}] {f['name']} ({f.get('modifiedTime', '')[:10]})"
                                                              for f in files), "Google Drive"),
                                  {"ids": [f["id"] for f in files]})
            if action == "drive_read":
                name, text = g.drive_read(str(inputs.get("id") or ""))
                return StepResult("done", f"{name}:\n" + untrusted(text, name))
        except GoogleError as e:
            return StepResult("failed", str(e))
        return StepResult("failed", "I can: events, add_event, move_event, drive_search, drive_read.")


UNDO_STORE = None        # set by the app


def event_handler(payload: dict) -> str:
    try:
        g = google()
        if payload.get("action") == "move_event":
            g.move_event(str(payload["id"]), float(payload["start"]), int(payload.get("minutes") or 60))
            return f"Moved it to {describe(float(payload['start']))}."
        made = g.add_event(str(payload["title"]), float(payload["start"]), int(payload.get("minutes") or 60),
                           str(payload.get("location") or ""))
        if UNDO_STORE is not None and isinstance(made, dict) and made.get("id"):
            from prometheus import undo

            undo.record(UNDO_STORE, "google_event", f"the calendar event '{payload['title']}'", {"id": made["id"]})
        return f"Added '{payload['title']}' {describe(float(payload['start']))}."
    except GoogleError as e:
        raise RuntimeError(str(e)) from e
