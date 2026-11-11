"""Assignment deadlines: from chosen college/school email senders and from what the owner says."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register

TRACKER = None
SERVICES = None


@register
class AssignmentsAgent(Agent):
    name = "assignments"
    description = ("Assignment and homework deadlines. Actions: add (title, due = when, e.g. 'Friday', '15 Nov 5pm'), "
                   "list (what's due soon), done (title words), scan (check college emails now). Each gets a "
                   "reminder two days before and on the day; it can go on the Google Calendar.")
    tools = {"system_read"}
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "system_read")
        if TRACKER is None:
            return StepResult("failed", "The assignment tracker isn't set up.")
        action = str(inputs.get("action") or "").lower()
        if action == "add":
            title, due = str(inputs.get("title") or "").strip(), str(inputs.get("due") or "").strip()
            said = TRACKER.tell(f"{title} due {due}")
            if said is None:
                return StepResult("failed", "When is it due? Say a day or date, like Friday or 15 Nov.")
            return self._calendar(ctx, said)
        if action == "list":
            items = TRACKER.upcoming(30)
            if not items:
                return StepResult("done", "Nothing due in the next 30 days that I know of.")
            from prometheus.assignments import _when

            return StepResult("done", "\n".join(f"- {a['title']}: {_when(a['due'])}" for a in items))
        if action == "done":
            return StepResult("done", TRACKER.mark_done(str(inputs.get("title") or goal)))
        if action == "scan":
            senders = list(getattr(SERVICES.settings, "assignment_senders", []) or []) if SERVICES else []
            if not senders:
                return StepResult("done", "Add your college or school email senders in Settings → Assignments first.")
            from prometheus.agents.email_agent import mailbox

            try:
                added = TRACKER.scan_email(mailbox(), senders)
            except Exception as e:
                return StepResult("failed", f"I couldn't read your email ({type(e).__name__}).")
            return StepResult("done", f"Found {len(added)} new deadline(s).")
        return StepResult("failed", "I can: add, list, done, scan.")

    def _calendar(self, ctx: AgentContext, said: str) -> StepResult:
        from prometheus.config import get_secret

        rows = TRACKER.upcoming(400)
        if not get_secret("GOOGLE_TOKEN") or not rows:
            return StepResult("done", said)
        newest = max(rows, key=lambda a: a["created"])
        res = ctx.ask("assignment_calendar", f"Put '{newest['title']}' on your Google Calendar",
                      {"id": newest["id"]}, exact=f"assignment:{newest['id']}", broad="assignment:any",
                      broad_label="Always put assignments on my calendar",
                      waiting=said + " Put it on your Google Calendar too? Answer below.")
        return res if res.status != "done" else StepResult("done", said + " " + res.summary)


def calendar_handler(payload: dict) -> str:
    from prometheus.agents.google_agent import google

    rows = TRACKER.store._rows("SELECT * FROM assignments WHERE id = ?", (int(payload["id"]),))
    if not rows:
        return "That assignment is gone."
    a = rows[0]
    google().add_event(f"Due: {a['title']}", a["due"] - 3600, 60)
    return "It's on your calendar."
