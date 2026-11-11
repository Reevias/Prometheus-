"""Alarms, timers and reminders, asked for in plain words."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.reminders import WhenError, describe, parse_when

MAX_TEXT = 300


@register
class Reminders(Agent):
    name = "reminders"
    description = ("Alarms, timers, reminders and standing goals. Actions: goal (text = the task to do by itself, "
                   "e.g. 'sort my Downloads folder'; when = e.g. 'every night at 11pm', 'every Monday at 9'), "
                   "set (text = what to remind about, e.g. 'call mom' or "
                   "'Alarm'; when = the time in the owner's words, e.g. 'in 10 minutes', 'at 6pm', 'tomorrow at 9', "
                   "'every day at 8'), list, cancel (text or id). They ring with a Windows notification, are spoken "
                   "if voice is on, and appear in this chat.")
    tools = {"system_read"}
    required_inputs = ("action",)

    def verify(self, goal: str, inputs: dict, result: StepResult, ctx: AgentContext) -> str:
        rid = result.data.get("id")
        if rid is None:
            return ""
        return "" if any(r["id"] == rid for r in ctx.store.reminders()) else "The reminder wasn't saved."

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "system_read")
        action = str(inputs.get("action") or "").strip().lower()
        chat_id = ctx.store.task(ctx.task_id)["chat_id"]
        if action == "goal":
            task = " ".join(str(inputs.get("text") or "").split())[:MAX_TEXT]
            if not task:
                return StepResult("failed", "What should I do each time? For example 'sort my Downloads folder'.")
            try:
                when = parse_when(str(inputs.get("when") or goal))
            except WhenError as e:
                return StepResult("failed", str(e))
            # A standing goal runs later as if the owner typed it, so it is asked in the chat first.
            again = {"daily": " and every day after", "weekdays": " and every weekday after", "weekly": " and every week after"}
            res = ctx.ask("standing_goal", f"Do this by myself {describe(when.due)}{again.get(when.repeat, '')}: “{task}”",
                          {"chat_id": chat_id, "task": task, "due": when.due, "repeat": when.repeat},
                          exact=f"goal:{task.lower()}:{when.repeat}",
                          waiting=f"Save the standing goal “{task}” {describe(when.due)}"
                                  f"{again.get(when.repeat, '')}? Answer below.")
            return res
        if action == "set":
            text = " ".join(str(inputs.get("text") or "Alarm").split())[:MAX_TEXT] or "Alarm"
            try:
                when = parse_when(str(inputs.get("when") or goal))
            except WhenError as e:
                return StepResult("failed", str(e))
            rid = ctx.store.add_reminder(chat_id, text, when.due, when.repeat)
            from prometheus import undo

            undo.record(ctx.store, "reminder", f"the reminder '{text}' {describe(when.due)}", {"id": rid})
            again = {"daily": ", then every day", "weekdays": ", then every weekday", "weekly": ", then every week"}
            return StepResult("done", f"Reminder set: '{text}' {describe(when.due)}{again.get(when.repeat, '')}.",
                              {"id": rid, "due": when.due})
        if action == "list":
            items = ctx.store.reminders()
            if not items:
                return StepResult("done", "No reminders waiting.")
            return StepResult("done", "Coming up:\n" + "\n".join(
                f"- {r['text']}: {describe(r['due'])}{' (repeats ' + r['repeat'] + ')' if r['repeat'] else ''}"
                for r in items))
        if action == "cancel":
            target = str(inputs.get("id") or inputs.get("text") or "").strip().lower()
            items = ctx.store.reminders()
            match = [r for r in items if str(r["id"]) == target] or [r for r in items if target and target in r["text"].lower()]
            if not match and len(items) == 1 and target in ("", "it", "that", "the alarm", "alarm", "reminder"):
                match = items
            if not match:
                return StepResult("failed", "I couldn't find that reminder. Say 'list my reminders' to see them.")
            for r in match:
                ctx.store.cancel_reminder(r["id"])
            return StepResult("done", "Cancelled: " + ", ".join(r["text"] for r in match) + ".")
        return StepResult("failed", f"'{action}' isn't a reminder action. I can: set, list, cancel.")


STORE = None        # set by the app, for goal_handler


def goal_handler(payload: dict) -> str:
    from prometheus import undo

    rid = STORE.add_reminder(int(payload["chat_id"]), str(payload["task"]), float(payload["due"]),
                             str(payload.get("repeat", "")), kind="goal")
    undo.record(STORE, "reminder", f"the standing goal '{payload['task']}'", {"id": rid})
    return f"Standing goal saved: '{payload['task']}' {describe(float(payload['due']))}. I'll run it by myself."
