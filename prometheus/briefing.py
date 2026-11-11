"""The daily summary: a morning briefing and an evening wrap-up.

Built on the laptop from facts (no AI call), so it costs nothing and email and calendar never leave the laptop:
calendar (today, or tomorrow in the evening), important email, weather (Open-Meteo, free, no key), reminders and
goals coming up, and what Prometheus did. Posted in the "Daily briefing" chat, shown as a notification, and spoken.
"""
from __future__ import annotations

import re
import threading
import time
from datetime import datetime, timedelta

import httpx

CHAT = "Daily briefing"
CHECK_EVERY = 120
NEWSLETTER = re.compile(r"no-?reply|newsletter|notifications?@|news@|marketing|promo|mailer|digest|updates?@|info@",
                        re.I)
WEATHER = {0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy", 51: "light drizzle",
           53: "drizzle", 55: "heavy drizzle", 61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow",
           73: "snow", 75: "heavy snow", 80: "rain showers", 81: "rain showers", 82: "heavy showers",
           95: "thunderstorms", 96: "thunderstorms with hail", 99: "thunderstorms with hail"}


def _at(now: float, hhmm: str) -> float:
    h, m = (int(x) for x in hhmm.split(":"))
    lt = time.localtime(now)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))


def weather(services, http: httpx.Client | None = None, day_offset: int = 0) -> str:
    """'Delhi: 24–31°C, light rain (60% chance)' or '' if it can't tell where or the service is down."""
    http = http or httpx.Client(timeout=15.0)
    s = services.settings
    lat = lon = None
    place = (s.weather_place or "").strip()
    try:
        if place:
            r = http.get("https://geocoding-api.open-meteo.com/v1/search", params={"name": place, "count": 1})
            hit = (r.json().get("results") or [None])[0]
            if hit:
                lat, lon, place = hit["latitude"], hit["longitude"], hit.get("name", place)
        elif getattr(services, "laptop", None) is not None:
            loc = services.laptop.location()
            lat, lon, place = loc["latitude"], loc["longitude"], "here"
        if lat is None:
            return ""
        r = http.get("https://api.open-meteo.com/v1/forecast", params={
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
            "daily": "temperature_2m_min,temperature_2m_max,weather_code,precipitation_probability_max"})
        d = r.json()["daily"]
        i = day_offset
        what = WEATHER.get(int(d["weather_code"][i]), "mixed")
        rain = d.get("precipitation_probability_max", [None] * 2)[i]
        return (f"{place}: {round(d['temperature_2m_min'][i])}–{round(d['temperature_2m_max'][i])}°C, {what}"
                + (f" ({rain}% chance of rain)" if rain else ""))
    except Exception:
        return ""


def _calendar(day_offset: int) -> list[str]:
    from prometheus.config import get_secret
    from prometheus.connectors.calendar_ics import events_between, fetch

    links = [u for u in (get_secret("CALENDAR_LINKS") or "").split("|") if u]
    if not links:
        return []
    events = []
    for url in links[:5]:
        try:
            events += fetch(url)
        except Exception:
            continue
    start = datetime.combine(datetime.now().date() + timedelta(days=day_offset), datetime.min.time())
    out = []
    for e in events_between(events, start, start + timedelta(days=1)):
        when = "all day" if e["all_day"] else f"{e['start']:%H:%M}"
        out.append(f"{when} {e['title']}" + (f" ({e['location']})" if e["location"] else ""))
    return out


def _email(services) -> tuple[int, list[str]]:
    from prometheus.agents.email_agent import mailbox

    if not services.settings.email_address:
        return 0, []
    try:
        unread = mailbox().search(unread=True, limit=30)
    except Exception:
        return 0, []
    chosen = [a.lower() for a in services.settings.email_alert_senders or []]
    people = [m for m in unread if not NEWSLETTER.search(m["from"])]
    important = [m for m in people if any(c in m["from"].lower() for c in chosen)] + \
                [m for m in people if not any(c in m["from"].lower() for c in chosen)]
    return len(unread), [f"{re.sub(r'<.*?>', '', m['from']).strip() or m['from']}: {m['subject']}"
                         for m in important[:5]]


def _done_today(services) -> list[str]:
    store = services.store
    since = time.time() - 86400
    rows = store._rows("SELECT status, text FROM tasks WHERE created > ?", (since,))
    done = sum(1 for r in rows if r["status"] == "done")
    failed = [r["text"][:60] for r in rows if r["status"] == "failed"]
    lines = []
    if rows:
        lines.append(f"{len(rows)} requests, {done} done" + (f", {len(failed)} didn't work out" if failed else ""))
        lines += [f"didn't work: {t}" for t in failed[:3]]
    made = store._rows("SELECT COUNT(*) AS n FROM outputs WHERE created > ? AND kind = 'created'", (since,))[0]["n"]
    if made:
        lines.append(f"{made} files or folders made")
    fired = store._rows("SELECT COUNT(*) AS n FROM messages WHERE created > ? AND model = 'reminder'", (since,))[0]["n"]
    if fired:
        lines.append(f"{fired} reminders rang")
    try:
        photos = store._rows("SELECT COUNT(*) AS n FROM photo_index WHERE read > ?", (since,))[0]["n"]
    except Exception:
        photos = 0
    if photos:
        lines.append(f"{photos} new photos looked at (say \"sort my photos\" to put them in folders)")
    try:
        from prometheus.agents import study_agent

        studied = study_agent.STUDY.summary_today() if study_agent.STUDY else ""
    except Exception:
        studied = ""
    if studied:
        lines.append(f"study: {studied}")
    try:
        from prometheus.agents import money_agent

        spent = money_agent.MONEY.today_line() if money_agent.MONEY else ""
    except Exception:
        spent = ""
    if spent:
        lines.append(f"money: {spent}")
    try:
        from prometheus.agents import assignments_agent

        soon = assignments_agent.TRACKER.soon_line(3) if assignments_agent.TRACKER else ""
    except Exception:
        soon = ""
    if soon:
        lines.append(f"due soon: {soon}")
    if any(a.kind == "add_skill" for a in store.big_approvals()):
        lines.append("a repo of the day is waiting on the Approvals page")
    return lines


def _coming(services, day_offset: int) -> list[str]:
    from prometheus.reminders import describe

    start = datetime.combine(datetime.now().date() + timedelta(days=day_offset), datetime.min.time()).timestamp()
    return [f"{describe(r['due'])}: {'goal: ' if r.get('kind') == 'goal' else ''}{r['text']}"
            for r in services.store.reminders() if start <= r["due"] < start + 86400][:6]


def build(services, kind: str = "morning", http: httpx.Client | None = None) -> str:
    evening = kind == "evening"
    day = 1 if evening else 0
    title = ("Evening wrap-up" if evening else "Good morning") + f", {datetime.now():%A %d %B}"
    parts = [f"## {title}"]
    w = weather(services, http, day_offset=day)
    if w:
        parts.append(("Tomorrow's weather: " if evening else "Weather: ") + w)
    cal = _calendar(day)
    label = "Tomorrow" if evening else "Today"
    parts.append(f"**{label} on your calendar**\n" + "\n".join(f"- {c}" for c in cal) if cal
                 else f"**{label} on your calendar**: nothing.")
    total, important = _email(services)
    if services.settings.email_address:
        parts.append(f"**Email**: {total} unread" + (":\n" + "\n".join(f"- {m}" for m in important) if important else "."))
    coming = _coming(services, day)
    if coming:
        parts.append(f"**Reminders and goals {label.lower()}**\n" + "\n".join(f"- {c}" for c in coming))
    if evening:
        did = _done_today(services)
        parts.append("**What I did today**\n" + "\n".join(f"- {d}" for d in did) if did
                     else "**What I did today**: nothing you asked for.")
    return "\n\n".join(parts)


def short_version(text: str) -> str:
    first = [l for l in text.splitlines() if l.startswith(("Weather", "Tomorrow's weather"))]
    events = sum(1 for l in text.splitlines() if l.startswith("- ") and ":" in l[:9])
    unread = re.search(r"\*\*Email\*\*: (\d+) unread", text)
    bits = [first[0].split(": ", 1)[1] if first else "",
            f"{events} things on the calendar" if events else "a free calendar",
            f"{unread.group(1)} unread emails" if unread else ""]
    return ". ".join(b for b in bits if b) + "."


class BriefingClock:
    def __init__(self, services, notify=None, clock=time.time):
        self.services, self.notify, self.clock = services, notify, clock
        self._stop = threading.Event()

    def chat(self) -> int:
        store = self.services.store
        for chat_id, title in store.chats():
            if title == CHAT:
                return chat_id
        chat_id = store.create_chat(CHAT)
        store.rename_chat(chat_id, CHAT, by="user")
        return chat_id

    def deliver(self, kind: str) -> str:
        text = build(self.services, kind)
        self.services.store.add_message(self.chat(), "assistant", text, "briefing", private=True)
        short = short_version(text)
        if self.notify is not None:
            try:
                self.notify(("Evening wrap-up: " if kind == "evening" else "Morning briefing: ") + short)
            except Exception:
                pass
        loop = getattr(getattr(self.services, "voice", None), "loop", None)
        focusing = getattr(getattr(self.services, "focus", None), "active", lambda: False)()
        if loop is not None and not loop.muted and not focusing:
            try:
                loop.speaker.say(("Good evening, sir. " if kind == "evening" else "Good morning, sir. ") + short)
            except Exception:
                pass
        return text

    def tick(self) -> list[str]:
        s, store, now = self.services.settings, self.services.store, self.clock()
        sent = []
        today = time.strftime("%Y-%m-%d", time.localtime(now))
        for kind, on, at in (("morning", s.briefing_morning_on, s.briefing_morning),
                             ("evening", s.briefing_evening_on, s.briefing_evening)):
            try:
                due = _at(now, at)
            except ValueError:
                continue
            key = f"briefing_{kind}"
            # within 3 hours of its time, once a day (a laptop that was asleep at 8:00 still gets it at 9:30)
            if on and due <= now < due + 3 * 3600 and store.kv_get(key) != today:
                store.kv_set(key, today)
                self.deliver(kind)
                sent.append(kind)
        sent += self.study_nudge(now, today)
        sent += self.money_check(now)
        sent += self.health_check(now)
        sent += self.exam_posts(now, today)
        sent += self.assignment_check(now)
        return sent

    def assignment_check(self, now: float) -> list[str]:
        """Every half hour: new deadlines in emails from the chosen college/school senders."""
        from prometheus import assignments as assign_mod
        from prometheus.agents import assignments_agent

        s, store, tr = self.services.settings, self.services.store, assignments_agent.TRACKER
        senders = list(getattr(s, "assignment_senders", []) or [])
        if tr is None or not senders or not s.email_address:
            return []
        if now - float(store.kv_get("assign_scanned", 0) or 0) < assign_mod.SCAN_EVERY:
            return []
        from prometheus.agents.email_agent import mailbox

        try:
            added = tr.scan_email(mailbox(), senders)
        except Exception:
            store.kv_set("assign_scanned", now)
            return []
        if not added:
            return []
        rows = [r for r in tr.upcoming(400) if r["id"] in added]
        text = "New deadlines from your email:\n" + "\n".join(
            f"- {r['title']}: {assign_mod._when(r['due'])}" for r in rows) + \
            "\nI'll remind you two days before each and on the day."
        store.add_message(tr.chat(), "assistant", text, "assignments", private=True)
        from prometheus.config import get_secret

        if get_secret("GOOGLE_TOKEN"):
            for r in rows:
                self.services.approvals.ask("assignment_calendar", f"Put '{r['title']}' on your Google Calendar",
                                            {"id": r["id"]}, (f"assignment:{r['id']}", "assignment:any"),
                                            chat_id=tr.chat(), broad_label="Always put assignments on my calendar")
        if self.notify is not None:
            try:
                self.notify(f"{len(rows)} new deadline(s): " + rows[0]["title"])
            except Exception:
                pass
        return ["assignments"]

    def exam_posts(self, now: float, today: str) -> list[str]:
        """Each day: today's revision topics at the study time, and the check-in in the evening."""
        import json as _json

        from prometheus.agents import exams_agent

        ex = exams_agent.EXAMS
        if ex is None:
            return []
        s, store, out = self.services.settings, self.services.store, []
        for key, at in (("exam_post", getattr(s, "exam_study_time", "17:00")),
                        ("exam_ask", getattr(s, "exam_checkin_time", "21:30"))):
            try:
                due = _at(now, at)
            except (ValueError, AttributeError):
                continue
            if not (due <= now < due + 3 * 3600) or store.kv_get(key) == today:
                continue
            store.kv_set(key, today)
            chat = self.study_chat()
            if key == "exam_post":
                text = ex.today_text()
                if text:
                    store.add_message(chat, "assistant", "📚 Today's revision:\n" + text, "exams")
                    if self.notify is not None:
                        try:
                            self.notify("Today's revision: " + text.splitlines()[0][:150])
                        except Exception:
                            pass
                    out.append("exam_post")
            else:
                q = ex.checkin_question()
                if q:
                    store.add_message(chat, "assistant", q[0], "exams")
                    store.kv_set("exam_checkin", _json.dumps({"chat": chat, "days": q[1], "asked": now}))
                    if self.notify is not None:
                        try:
                            self.notify("Did you finish today's revision? Answer in the Study chat.")
                        except Exception:
                            pass
                    out.append("exam_ask")
        return out

    def health_check(self, now: float) -> list[str]:
        """Once a week (Sunday 19:00 by default): the laptop health check."""
        s, store = self.services.settings, self.services.store
        day = int(getattr(s, "health_day", 6))
        if day < 0 or time.localtime(now).tm_wday != day:
            return []
        try:
            at = _at(now, getattr(s, "health_time", "19:00") or "19:00")
        except ValueError:
            return []
        if not (at <= now < at + 3 * 3600) or now - float(store.kv_get("health_last", 0) or 0) < 5 * 86400:
            return []
        from prometheus import health

        report = health.post(self.services, getattr(self.services, "laptop", None), now)
        if self.notify is not None:
            try:
                self.notify(report.splitlines()[0])
            except Exception:
                pass
        return ["health"]

    def money_check(self, now: float) -> list[str]:
        """Every half hour: new bank and UPI alerts from the email, the spending sheet, and budget warnings."""
        s, store = self.services.settings, self.services.store
        from prometheus import money as money_mod
        from prometheus.agents import money_agent

        m = money_agent.MONEY
        if m is None or not getattr(s, "money_on", True) or not s.email_address:
            return []
        if now - float(store.kv_get("money_scanned", 0) or 0) < money_mod.SCAN_EVERY:
            return []
        from prometheus.agents.email_agent import mailbox

        try:
            added = m.scan_email(mailbox())
        except Exception:
            store.kv_set("money_scanned", now)
            return []
        if added:
            try:
                m.write_excel(self.services.home / "Documents" / "Prometheus Money")
            except Exception:
                pass
        for warning in m.budget_alerts():
            store.add_message(self.chat(), "assistant", f"💸 {warning}", "money", private=True)
            if self.notify is not None:
                try:
                    self.notify(warning)
                except Exception:
                    pass
        return ["money"] if added else []

    def study_nudge(self, now: float, today: str) -> list[str]:
        """Once a day at the study time: tell the owner how many flashcards are due."""
        s, store = self.services.settings, self.services.store
        at = getattr(s, "study_reminder", "") or ""
        try:
            due_at = _at(now, at) if at else None
        except ValueError:
            due_at = None
        if due_at is None or not (due_at <= now < due_at + 3 * 3600) or store.kv_get("study_nudge") == today:
            return []
        from prometheus.agents import study_agent

        if study_agent.STUDY is None:
            return []
        due = len(study_agent.STUDY.due())
        store.kv_set("study_nudge", today)
        if not due:
            return []
        text = f"{due} flashcard(s) are due for review. Say \"quiz me\" when you're ready."
        store.add_message(self.study_chat(), "assistant", text, "study")
        if self.notify is not None:
            try:
                self.notify(text)
            except Exception:
                pass
        return ["study"]

    def study_chat(self) -> int:
        from prometheus.study import CHAT as STUDY_CHAT

        store = self.services.store
        for chat_id, title in store.chats():
            if title == STUDY_CHAT:
                return chat_id
        chat_id = store.create_chat(STUDY_CHAT)
        store.rename_chat(chat_id, STUDY_CHAT, by="user")
        return chat_id

    def loop(self) -> None:
        while not self._stop.wait(CHECK_EVERY):
            try:
                self.tick()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-briefing", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
