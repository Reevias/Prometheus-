"""Calendars through their private iCal link (Google: Settings → your calendar → "Secret address in iCal format";
Outlook: Settings → Calendar → Shared calendars → Publish → ICS). Read only. Repeating events (daily, weekly,
monthly, yearly) are expanded; times in UTC are shown in laptop time.
"""
from __future__ import annotations

import calendar as cal
import re
from datetime import date, datetime, timedelta, timezone

import httpx

DAYS = {"MO": 0, "TU": 1, "WE": 2, "TH": 3, "FR": 4, "SA": 5, "SU": 6}
MAX_REPEATS = 800


class CalendarError(Exception):
    pass


def _unfold(text: str) -> list[str]:
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        if raw[:1] in (" ", "\t") and lines:
            lines[-1] += raw[1:]
        else:
            lines.append(raw)
    return lines


def _when(value: str, params: str) -> tuple[datetime, bool]:
    """(local naive datetime, all_day)."""
    value = value.strip()
    if "VALUE=DATE" in params or re.fullmatch(r"\d{8}", value):
        return datetime.strptime(value[:8], "%Y%m%d"), True
    dt = datetime.strptime(value[:15], "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        dt = dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return dt, False


def _text(value: str) -> str:
    out = value
    for a, b in (("\\n", " "), ("\\N", " "), ("\\,", ","), ("\\;", ";"), ("\\\\", "\\")):
        out = out.replace(a, b)
    return out.strip()


def parse_ics(text: str) -> list[dict]:
    events, cur = [], None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            if "start" in cur:
                events.append(cur)
            cur = None
        elif cur is not None and ":" in line:
            name_params, value = line.split(":", 1)
            name, _, params = name_params.partition(";")
            name = name.upper()
            if name == "DTSTART":
                cur["start"], cur["all_day"] = _when(value, params)
            elif name == "DTEND":
                cur["end"], _ = _when(value, params)
            elif name in ("SUMMARY", "LOCATION", "DESCRIPTION"):
                cur[name.lower()] = _text(value)[:500]
            elif name == "RRULE":
                cur["rrule"] = dict(p.split("=", 1) for p in value.split(";") if "=" in p)
            elif name == "EXDATE":
                cur.setdefault("exdates", set()).update(_when(v, params)[0] for v in value.split(","))
            elif name == "STATUS" and value.strip().upper() == "CANCELLED":
                cur["cancelled"] = True
    return [e for e in events if not e.get("cancelled")]


def _repeats(ev: dict, until: datetime):
    """Start times of an event (with its repeats) up to `until`."""
    start, rule = ev["start"], ev.get("rrule")
    if not rule:
        yield start
        return
    freq = rule.get("FREQ", "")
    step = max(1, int(rule.get("INTERVAL", "1") or 1))
    count = int(rule["COUNT"]) if rule.get("COUNT", "").isdigit() else None
    last = _when(rule["UNTIL"], "")[0] if rule.get("UNTIL") else until
    byday = [DAYS[d[-2:]] for d in rule.get("BYDAY", "").split(",") if d[-2:] in DAYS]
    n, cur = 0, start
    while cur <= min(until, last) and n < MAX_REPEATS and (count is None or n < count):
        if freq == "WEEKLY" and byday:
            week = cur - timedelta(days=cur.weekday())
            for d in sorted(byday):
                t = week + timedelta(days=d)
                if start <= t <= min(until, last) and (count is None or n < count):
                    n += 1
                    yield t
            cur = week + timedelta(weeks=step) + timedelta(days=start.weekday())
            cur = cur.replace(hour=start.hour, minute=start.minute)
            continue
        n += 1
        yield cur
        if freq == "DAILY":
            cur += timedelta(days=step)
        elif freq == "WEEKLY":
            cur += timedelta(weeks=step)
        elif freq == "MONTHLY":
            m = cur.month - 1 + step
            y, m = cur.year + m // 12, m % 12 + 1
            cur = cur.replace(year=y, month=m, day=min(start.day, cal.monthrange(y, m)[1]))
        elif freq == "YEARLY":
            cur = cur.replace(year=cur.year + step)
        else:
            return


def events_between(events: list[dict], start: datetime, end: datetime) -> list[dict]:
    out = []
    for ev in events:
        length = (ev.get("end") or ev["start"]) - ev["start"]
        for t in _repeats(ev, end):
            if t in ev.get("exdates", set()):
                continue
            finish = t + length
            if finish > start and t < end or (ev.get("all_day") and t.date() >= start.date() and t < end):
                out.append({"title": ev.get("summary", "(no title)"), "start": t, "end": finish,
                            "all_day": ev.get("all_day", False), "location": ev.get("location", "")})
    return sorted(out, key=lambda e: e["start"])


def day_range(word: str, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    now = now or datetime.now()
    today = datetime.combine(now.date(), datetime.min.time())
    word = (word or "today").lower()
    if "tomorrow" in word:
        return today + timedelta(days=1), today + timedelta(days=2), "tomorrow"
    if "week" in word:
        return today, today + timedelta(days=7), "the next 7 days"
    if "month" in word:
        return today, today + timedelta(days=31), "the next month"
    return today, today + timedelta(days=1), "today"


def fetch(url: str, http: httpx.Client | None = None) -> list[dict]:
    if not re.match(r"^(https|webcal)://", url or ""):
        raise CalendarError("The calendar link should start with https:// (or webcal://).")
    url = "https://" + url.split("://", 1)[1]
    http = http or httpx.Client(timeout=30.0, follow_redirects=True)
    try:
        resp = http.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise CalendarError(f"Couldn't get the calendar ({type(e).__name__}). Check the link in Settings.") from e
    if "BEGIN:VCALENDAR" not in resp.text[:2000]:
        raise CalendarError("That link didn't return a calendar. Use the secret iCal (.ics) address.")
    return parse_ics(resp.text)


def describe(ev: dict) -> str:
    when = "all day" if ev["all_day"] else f"{ev['start']:%H:%M}–{ev['end']:%H:%M}"
    return f"{ev['start']:%a %d %b} {when}: {ev['title']}" + (f" ({ev['location']})" if ev["location"] else "")


