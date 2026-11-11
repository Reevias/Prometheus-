"""What Prometheus says after a double clap: a greeting and a short spoken brief.

Time and weather, today's plan (calendar, reminders, assignments due soon), messages (important email, phone
notifications), and money and study. Each part is fetched at the same time with a time limit, so a slow
email server or a phone that is off never holds the greeting up. Built from facts, with no AI call.
"""
from __future__ import annotations

import concurrent.futures as cf
import re
import time
from datetime import datetime

WAIT = 5.0          # seconds the brief waits for the slow parts (email, weather, phone)


def greeting(now: datetime | None = None) -> str:
    now = now or datetime.now()
    part = "morning" if 4 <= now.hour < 12 else "afternoon" if now.hour < 17 else "evening"
    clock = now.strftime("%I:%M %p").lstrip("0")
    return f"Good {part} sir. It's {clock}."


def _weather(services) -> str:
    from prometheus.briefing import weather

    w = weather(services)
    if not w:
        return ""
    place, _, rest = w.partition(": ")
    rest = rest.replace("–", " to ").replace("°C", " degrees")
    return (f"In {place}, it's {rest}." if place and place != "here" else f"Today it's {rest}.")


def _plan(services) -> str:
    from prometheus.briefing import _calendar, _coming

    bits = []
    try:
        cal = _calendar(0)
    except Exception:
        cal = []
    if cal:
        first = ", ".join(cal[:2])
        bits.append(f"You have {len(cal)} thing{'s' if len(cal) != 1 else ''} on your calendar: {first}"
                    + (", and more." if len(cal) > 2 else "."))
    try:
        coming = _coming(services, 0)
    except Exception:
        coming = []
    if coming:
        bits.append("Reminders: " + "; ".join(coming[:2]) + ".")
    try:
        from prometheus.agents import assignments_agent

        soon = assignments_agent.TRACKER.soon_line(3) if assignments_agent.TRACKER else ""
    except Exception:
        soon = ""
    if soon:
        bits.append(f"Due soon: {soon}.")
    return " ".join(bits) or "Your day is clear."


def _messages(services) -> str:
    bits = []
    try:
        from prometheus.briefing import _email

        total, important = _email(services)
    except Exception:
        total, important = 0, []
    if total:
        who = [re.sub(r"\s*:.*$", "", m) for m in important[:2]]
        bits.append(f"{total} unread email{'s' if total != 1 else ''}" + (f", including {' and '.join(who)}" if who
                                                                              else "") + ".")
    target = getattr(services.settings, "phone_target", "") or ""
    if target:
        try:
            from prometheus.connectors.android import Phone

            items = Phone(target).notifications()
        except Exception:
            items = []
        if items:
            apps = []
            for i in items:
                name = i["app"].split(".")[-1].capitalize()
                if name not in apps:
                    apps.append(name)
            bits.append(f"{len(items)} notification{'s' if len(items) != 1 else ''} on your phone, from "
                        + ", ".join(apps[:3]) + ".")
    return " ".join(bits)


def _money_and_study(services) -> str:
    bits = []
    try:
        from prometheus.agents import money_agent

        m = money_agent.MONEY
        if m is not None:
            now = m.clock()
            spent = sum(m.by_category(m.month_start(now)).values())
            budgets = getattr(services.settings, "money_budgets", {}) or {}
            limit = sum(budgets.values()) if budgets else 0
            if limit:
                bits.append(f"You've spent {round(100 * spent / limit)} percent of this month's budget.")
            elif spent:
                bits.append(f"You've spent {spent:,.0f} rupees this month.")
    except Exception:
        pass
    try:
        from prometheus.agents import study_agent

        due = len(study_agent.STUDY.due()) if study_agent.STUDY is not None else 0
    except Exception:
        due = 0
    if due:
        bits.append(f"{due} flashcard{'s are' if due != 1 else ' is'} due: say quiz me when you're ready.")
    else:
        try:
            from prometheus.agents import exams_agent

            plan = exams_agent.EXAMS
            today = plan.today_text() if plan is not None else ""
        except Exception:
            today = ""
        if today:
            bits.append("Study today: " + today.splitlines()[0] + ".")
    return " ".join(bits)


def build(services, now: datetime | None = None, wait: float = WAIT) -> str:
    parts = [greeting(now)]
    jobs = (("weather", _weather), ("plan", _plan), ("messages", _messages), ("money", _money_and_study))
    pool = cf.ThreadPoolExecutor(max_workers=len(jobs), thread_name_prefix="prometheus-brief")
    futures = {name: pool.submit(fn, services) for name, fn in jobs}
    deadline = time.monotonic() + wait
    for name, _ in jobs:
        try:
            text = futures[name].result(timeout=max(0.05, deadline - time.monotonic()))
        except Exception:
            text = ""
        if text:
            parts.append(text)
    pool.shutdown(wait=False, cancel_futures=True)
    parts.append("What would you like to do?")
    return " ".join(parts)
