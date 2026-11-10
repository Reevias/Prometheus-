"""Alarms, timers and reminders: "alarm for 10 mins", "remind me at 6 to call mom", "every day at 8 tell me the
weather". They survive restarts (kept in memory.db). When one is due Prometheus shows a Windows notification,
says it out loud if voice is on, and posts it in the chat it came from.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass

CHECK_EVERY = 5               # seconds
WORDS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty": 40, "forty five": 45, "half an": 30}
UNITS = {"s": 1, "sec": 1, "secs": 1, "second": 1, "seconds": 1, "m": 60, "min": 60, "mins": 60, "minute": 60,
         "minutes": 60, "h": 3600, "hr": 3600, "hrs": 3600, "hour": 3600, "hours": 3600, "day": 86400, "days": 86400}
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
REPEATS = ("", "daily", "weekdays", "weekly")


class WhenError(ValueError):
    pass


@dataclass
class When:
    due: float
    repeat: str = ""


def _at(now: float, hour: int, minute: int, day_offset: int = 0) -> float:
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday + day_offset, hour, minute, 0, 0, 0, -1))


def _clock(text: str) -> tuple[int, int] | None:
    """'6', '6pm', '6:30 am', '18:45', 'noon', 'midnight' → (hour, minute)."""
    t = text.strip().lower().replace(".", "")
    if t in ("noon", "midday"):
        return 12, 0
    if t == "midnight":
        return 0, 0
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", t)
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if half == "pm" and hour < 12:
        hour += 12
    if half == "am" and hour == 12:
        hour = 0
    if hour > 23 or minute > 59:
        return None
    return hour, minute


def parse_when(text: str, now: float | None = None) -> When:
    """Understand when a reminder is due. Raises WhenError if it can't tell."""
    now = time.time() if now is None else now
    t = " ".join((text or "").lower().replace(",", " ").split())
    repeat = ""
    if re.search(r"\bevery ?day\b|\bdaily\b|\beach day\b|\bevery morning\b|\bevery evening\b|\bevery night\b", t):
        repeat = "daily"
    elif re.search(r"\bweekdays\b|\bevery weekday\b", t):
        repeat = "weekdays"
    elif re.search(r"\bevery (mon|tues|wednes|thurs|fri|satur|sun)day\b|\bweekly\b", t):
        repeat = "weekly"

    m = re.search(r"\b(?:in|for|after)\s+(\d+(?:\.\d+)?|" + "|".join(sorted(WORDS, key=len, reverse=True))
                  + r")\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|s|m|h)\b", t)
    if m:
        n = float(WORDS.get(m.group(1), m.group(1)) if m.group(1) in WORDS else m.group(1))
        return When(now + n * UNITS[m.group(2)], "")
    m = re.search(r"\b(\d+(?:\.\d+)?)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", t)
    if m and not re.search(r"\bat\b", t):                      # "10 minute timer", "alarm 5 mins"
        return When(now + float(m.group(1)) * UNITS[m.group(2)], "")

    clock = None
    m = re.search(r"\b(?:at|by|for)\s+(noon|midday|midnight|\d{1,2}(?::\d{2})?\s*(?:am|pm)?)\b", t)
    if m:
        clock = _clock(m.group(1))
    if clock is None:
        m = re.search(r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|noon|midnight)\b", t)
        clock = _clock(m.group(1)) if m else None
    if clock is None and re.search(r"\b(tomorrow|morning|evening|tonight)\b", t):
        clock = (8, 0) if "morning" in t else (19, 0) if "evening" in t else (21, 0) if "tonight" in t else (9, 0)
    if clock is None:
        raise WhenError("I couldn't tell when. Say something like 'in 10 minutes', 'at 6pm' or 'tomorrow at 9'.")
    hour, minute = clock
    if hour < 12 and not re.search(r"\d\s*(am|pm)\b|\bmorning\b", t) and re.search(r"\b(evening|tonight|night)\b", t):
        hour += 12
    day = 0
    if "tomorrow" in t:
        day = 1
    else:
        for i, name in enumerate(DAYS):
            if re.search(rf"\b{name}\b", t):
                day = (i - time.localtime(now).tm_wday) % 7 or 7
                break
    due = _at(now, hour, minute, day)
    ambiguous = hour < 12 and not re.search(r"\d\s*(am|pm)\b|\bmorning\b", t)
    if due <= now and day == 0 and ambiguous and _at(now, hour + 12, minute) > now:
        due = _at(now, hour + 12, minute)         # "at 6" at 3pm means 6pm today, not 6am tomorrow
    if due <= now:                       # that time already passed today: the next one
        due = _at(now, hour, minute, day + 1)
    if repeat == "weekdays":
        while time.localtime(due).tm_wday >= 5:
            due += 86400
    return When(due, repeat)


def next_time(due: float, repeat: str) -> float | None:
    if repeat == "daily":
        return due + 86400
    if repeat == "weekly":
        return due + 7 * 86400
    if repeat == "weekdays":
        nxt = due + 86400
        while time.localtime(nxt).tm_wday >= 5:
            nxt += 86400
        return nxt
    return None


def describe(due: float, now: float | None = None) -> str:
    now = time.time() if now is None else now
    left = due - now
    clock = time.strftime("%H:%M", time.localtime(due))
    if left < 3600:
        mins = max(1, round(left / 60))
        return f"in {mins} minute{'s' if mins != 1 else ''} ({clock})"
    if time.localtime(due).tm_yday == time.localtime(now).tm_yday:
        return f"today at {clock}"
    if 0 < left < 2 * 86400 and time.localtime(due).tm_yday == time.localtime(now + 86400).tm_yday:
        return f"tomorrow at {clock}"
    return time.strftime("%a %d %b at %H:%M", time.localtime(due))


class ReminderClock:
    """Checks every few seconds and fires what is due. Missed while the laptop was off: fired at the next start."""

    def __init__(self, services, notify=None, clock=time.time):
        self.services = services
        self.notify = notify
        self.clock = clock
        self._stop = threading.Event()

    def fire(self, r: dict) -> None:
        store = self.services.store
        late = self.clock() - r["due"] > 120
        text = f"⏰ Reminder: {r['text']}" + (" (this was due while Prometheus was off)" if late else "")
        store.add_message(r["chat_id"], "assistant", text, "reminder")
        if self.notify is not None:
            try:
                self.notify(r["text"])
            except Exception:
                pass
        voice = getattr(self.services, "voice", None)
        loop = getattr(voice, "loop", None)
        if loop is not None and not loop.muted:
            try:
                loop.speaker.say(f"Reminder, sir: {r['text']}")
            except Exception:
                pass
        nxt = next_time(r["due"], r["repeat"])
        while nxt is not None and nxt <= self.clock():
            nxt = next_time(nxt, r["repeat"])
        store.reminder_fired(r["id"], nxt)

    def tick(self) -> int:
        due = self.services.store.reminders_due(self.clock())
        for r in due:
            self.fire(r)
        return len(due)

    def loop(self) -> None:
        while not self._stop.wait(CHECK_EVERY):
            try:
                self.tick()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-reminders", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
