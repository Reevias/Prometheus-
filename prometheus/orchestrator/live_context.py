"""Live context (M3.6): one short line the AI gets with every message, so it knows what is going on right now.

Owner's choice: the time and COUNTS only (reminders due today, tasks running, approvals waiting, the battery). No
reminder, calendar or task titles, so nothing personal goes to an online AI through this line. It is built fresh
each time from the real state and never guesses: anything it can't read is left out.
"""
from __future__ import annotations

import time


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def reminders_due_today(store, now: float | None = None) -> int:
    now = time.time() if now is None else now
    end = time.mktime(time.localtime(now)[:3] + (23, 59, 59, 0, 0, -1))
    try:
        rows = store._rows("SELECT COUNT(*) AS n FROM reminders WHERE state = 'waiting' AND due >= ? AND due <= ?",
                           (now, end))
        return int(rows[0]["n"]) if rows else 0
    except Exception:
        return -1


def battery() -> str:
    try:
        import psutil

        b = psutil.sensors_battery()
    except Exception:
        return ""
    if b is None:
        return ""
    return f"battery {int(b.percent)}%" + (" (charging)" if b.power_plugged else "")


def line(services, now: float | None = None) -> str:
    """'Right now: 2 reminders due today; 1 task running; 3 approvals waiting; battery 34%.' (only what is known)"""
    parts = []
    due = reminders_due_today(services.store, now)
    if due >= 0:
        parts.append(_plural(due, "reminder") + " due today")
    running = len(getattr(services, "running", {}) or {})
    parts.append(_plural(running, "task") + " running")
    try:
        parts.append(_plural(len(services.store.big_approvals()), "approval") + " waiting for the owner")
    except Exception:
        pass
    b = battery()
    if b:
        parts.append(b)
    return "Right now: " + "; ".join(parts) + "." if parts else ""
