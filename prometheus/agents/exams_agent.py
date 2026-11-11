"""Exam countdown planner: a day-by-day revision plan from exam dates and the syllabus."""
from __future__ import annotations

from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.agents.textutil import extract_text
from prometheus.exams import parse_date, topics_from_text
from prometheus.safety.paths import PathBlocked

EXAMS = None
SERVICES = None


@register
class ExamsAgent(Agent):
    name = "exams"
    description = ("Exam revision planner. Actions: plan (exam = its name, date = the exam date, paths = the syllabus "
                   "PDF or notes, or topics = the topics as a list or text), show (exam, or empty for all plans), "
                   "today (today's topics). Study blocks go on the Google Calendar after a yes; every evening "
                   "Prometheus asks what got done and moves the rest.")
    tools = {"read_files", "call_model"}
    accepts_private = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        if EXAMS is None:
            return StepResult("failed", "The exam planner isn't set up.")
        action = str(inputs.get("action") or "").lower()
        if action == "plan":
            exam = str(inputs.get("exam") or "Exam").strip()[:80]
            when = parse_date(str(inputs.get("date") or ""), EXAMS.today())
            if when is None:
                return StepResult("failed", "When is the exam? Say a date like 20 March or 2027-03-20.")
            text = ""
            raw = inputs.get("paths") or inputs.get("path") or []
            for p in [raw] if isinstance(raw, str) else raw:
                try:
                    text += extract_text(ctx.policy.check_read(Path(str(p)).expanduser()), limit=40_000) + "\n"
                except PathBlocked as e:
                    return StepResult("failed", f"I can't read {p}: {e}")
            topics_raw = inputs.get("topics")
            topics = list(topics_raw) if isinstance(topics_raw, list) else topics_from_text(str(topics_raw or ""))
            if text.strip():
                topics += EXAMS.topics_from_syllabus(text)
            topics = list(dict.fromkeys(t for t in topics if t))
            if not topics:
                return StepResult("failed", "What's on the syllabus? Give me the syllabus file or list the topics.")
            try:
                pid = EXAMS.create(exam, when, topics)
            except ValueError as e:
                return StepResult("failed", str(e))
            plan = EXAMS.show(pid)
            s = getattr(SERVICES, "settings", None)
            at = getattr(s, "exam_study_time", "17:00") or "17:00"
            from prometheus.config import get_secret

            if get_secret("GOOGLE_TOKEN"):
                n = len(EXAMS.days(pid))
                res = ctx.ask("exam_calendar", f"Put {n} study blocks for {exam} on your Google Calendar (daily at {at})",
                              {"plan_id": pid}, exact=f"exam_calendar:{pid}",
                              waiting=f"Here's the plan:\n{plan}\n\nPut the {n} study blocks on your Google "
                                      "Calendar? Answer below.")
                return res
            return StepResult("done", plan + f"\n\nEach day at {at} I'll post that day's topics, and every evening "
                                             "I'll ask what you finished. (Connect Google in Settings to get the "
                                             "study blocks on your calendar.)")
        if action == "show":
            plans = EXAMS.plans()
            name = str(inputs.get("exam") or "").lower()
            plans = [p for p in plans if name in p["exam"].lower()] or plans
            if not plans:
                return StepResult("done", "No exam plans yet. Tell me the exam, its date and the syllabus.")
            return StepResult("done", "\n\n".join(EXAMS.show(p["id"]) for p in plans))
        if action == "today":
            return StepResult("done", EXAMS.today_text() or "Nothing planned for today.")
        return StepResult("failed", "I can: plan, show, today.")


def calendar_handler(payload: dict) -> str:
    from prometheus.agents.google_agent import google

    s = getattr(SERVICES, "settings", None)
    hh, mm = (int(x) for x in (getattr(s, "exam_study_time", "17:00") or "17:00").split(":"))
    minutes = int(getattr(s, "exam_block_minutes", 120) or 120)
    g = google()
    added = 0
    for d in EXAMS.days(int(payload["plan_id"])):
        start = datetime.combine(date.fromisoformat(d["day"]), dtime(hh, mm)).timestamp()
        title = ("Revision: " if d["kind"] == "revise" else "Study: ") + ", ".join(d["topics"])[:80]
        g.add_event(title, start, minutes)
        added += 1
    return f"Added {added} study blocks to your Google Calendar."
