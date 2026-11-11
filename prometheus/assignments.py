"""Assignment tracker: deadlines from college or school emails (only the senders the owner chose) and from what
they say ("physics lab report due Friday"). Each one gets a reminder two days before and on the morning it's due,
can go on the Google Calendar (asked in the chat, or always), and shows in the evening wrap-up.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timedelta

SCHEMA = ("CREATE TABLE IF NOT EXISTS assignments (id INTEGER PRIMARY KEY, title TEXT NOT NULL, due REAL NOT NULL, "
          "source TEXT NOT NULL, ref TEXT NOT NULL UNIQUE, done INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL)")
CHAT = "Assignments"
SCAN_EVERY = 1800
DUE_WORDS = re.compile(r"\b(due|deadline|submit(?:ted)?(?: it)? by|submission|last date|hand in|turn in)\b", re.I)
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DATE_BIT = (r"(?:\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/-]\d{1,2}(?:[/-]\d{2,4})?|\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|"
            r"may|jun|jul|aug|sep|oct|nov|dec)[a-z]*(?:\s+\d{4})?|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)"
            r"[a-z]*\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s+\d{4})?|today|tonight|tomorrow|(?:next\s+)?(?:" + "|".join(WEEKDAYS)
            + r"))")
TIME_BIT = r"(?:\s*(?:at|by|before)?\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm)|\d{1,2}:\d{2}|midnight|noon))?"
WHEN = re.compile(DATE_BIT + TIME_BIT, re.I)
TELL = re.compile(r"^(?:my\s+|the\s+|i have (?:a|an|my)\s+)?(.+?)\s+(?:is\s+)?(?:due|deadline is|has to be submitted|"
                  r"to submit)\s+(?:on\s+|by\s+|before\s+)?(.+)$", re.I)


def parse_due(text: str, now: float) -> float | None:
    """The first deadline-looking date (and time) in the text, as a timestamp; 23:59 when no time is given."""
    from prometheus.exams import parse_date
    from prometheus.reminders import _clock

    rel = re.search(r"\bin (\d{1,3}) (day|week)s?\b", text or "", re.I)
    if rel:
        day = datetime.fromtimestamp(now).date() + timedelta(days=int(rel.group(1)) * (7 if rel.group(2).lower() == "week" else 1))
        return datetime(day.year, day.month, day.day, 23, 59).timestamp()
    m = WHEN.search(text or "")
    if not m:
        return None
    raw = m.group(0)
    date_part = re.sub(TIME_BIT + "$", "", raw, flags=re.I).strip().lower()
    today = datetime.fromtimestamp(now).date()
    if date_part in ("today", "tonight"):
        day = today
    elif date_part == "tomorrow":
        day = today + timedelta(days=1)
    elif date_part.split()[-1] in WEEKDAYS:
        target = WEEKDAYS.index(date_part.split()[-1])
        ahead = (target - today.weekday()) % 7 or 7
        day = today + timedelta(days=ahead)          # "next Monday" = the coming Monday
    else:
        clean = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", date_part).replace(",", "")
        if re.fullmatch(r"\d{1,2}-\d{1,2}", clean):
            clean = clean.replace("-", "/")
        day = parse_date(clean, today)
        if day is None:
            try:
                d, mth = (int(x) for x in clean.split("/")[:2])
                day = datetime(today.year, mth, d).date()
                if day < today:
                    day = day.replace(year=today.year + 1)
            except (ValueError, IndexError):
                return None
    hh, mm = 23, 59
    if m.group(1):
        got = _clock(m.group(1)) if m.group(1) not in ("midnight", "noon") else ((23, 59) if m.group(1) == "midnight"
                                                                                  else (12, 0))
        if got:
            hh, mm = got
    return datetime(day.year, day.month, day.day, hh, mm).timestamp()


def clean_title(subject: str) -> str:
    t = subject or ""
    for _ in range(3):
        t = re.sub(r"^(re|fwd?|reminder|announcement|new assignment|assignment posted)\s*:\s*", "", t, flags=re.I)
    t = re.sub(r"\s*[-–|:]\s*(due|deadline).*$", "", t, flags=re.I)
    return " ".join(t.split())[:100] or "Assignment"


class Assignments:
    def __init__(self, store, settings=None, clock=time.time):
        self.store, self.settings, self.clock = store, settings, clock
        with store._lock:
            store._db.execute(SCHEMA)
            store._db.commit()

    def chat(self) -> int:
        for chat_id, title in self.store.chats():
            if title == CHAT:
                return chat_id
        chat_id = self.store.create_chat(CHAT)
        self.store.rename_chat(chat_id, CHAT, by="user")
        return chat_id

    def add(self, title: str, due: float, source: str = "told", ref: str = "") -> int | None:
        ref = ref or f"{source}:{title.lower()}:{int(due // 3600)}"
        try:
            aid = self.store._write("INSERT INTO assignments (title, due, source, ref, created) VALUES (?, ?, ?, ?, ?)",
                                    (title[:100], due, source, ref, self.clock()))
        except Exception:
            return None                                  # already known
        chat, now = self.chat(), self.clock()
        two_days = due - 2 * 86400
        lt = time.localtime(two_days)
        nag = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 18, 0, 0, 0, 0, -1))
        if nag > now:
            self.store.add_reminder(chat, f"{title} is due in two days ({_when(due)})", nag, "")
        lt = time.localtime(due)
        morning = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0, 0, 0, 0, -1))
        if now < morning < due:
            self.store.add_reminder(chat, f"{title} is due today ({_when(due)})", morning, "")
        return aid

    def tell(self, text: str) -> str | None:
        m = TELL.match(" ".join((text or "").split()).strip(" .!"))
        if not m:
            return None
        due = parse_due(m.group(2), self.clock())
        if due is None:
            return None
        title = m.group(1).strip()
        title = title[0].upper() + title[1:]
        if self.add(title, due) is None:
            return f"I already have {title}."
        return f"Got it: {title}, due {_when(due)}. I'll remind you two days before and on the day."

    def upcoming(self, days: float = 30) -> list[dict]:
        now = self.clock()
        return [dict(r) for r in self.store._rows("SELECT * FROM assignments WHERE done = 0 AND due > ? AND due < ? "
                                                  "ORDER BY due", (now - 86400, now + days * 86400))]

    def mark_done(self, words: str) -> str:
        words_l = [w for w in re.findall(r"\w{3,}", (words or "").lower())]
        for a in self.upcoming(365):
            if words_l and all(w in a["title"].lower() for w in words_l):
                self.store._write("UPDATE assignments SET done = 1 WHERE id = ?", (a["id"],))
                for r in self.store.reminders():
                    if a["title"] in r["text"]:
                        self.store.cancel_reminder(r["id"])
                return f"Marked {a['title']} as done. Nice!"
        return "I couldn't find that assignment."

    def soon_line(self, days: int = 3) -> str:
        items = self.upcoming(days)
        return "; ".join(f"{a['title']} ({_when(a['due'])})" for a in items)

    def scan_email(self, mailbox, senders: list[str], days: int = 14) -> list[int]:
        added = []
        for sender in senders[:20]:
            try:
                found = mailbox.search(sender=sender, days=days, limit=30)
            except Exception:
                continue
            for msg in found:
                import email.utils

                from prometheus.watchers import _sender_matches

                if not _sender_matches(email.utils.parseaddr(msg.get("from", ""))[1].lower(), sender):
                    continue                                  # a display name pretending to be the college
                text = f"{msg.get('subject', '')}. {msg.get('snippet', '')}"
                if not DUE_WORDS.search(text):
                    continue
                after = text[DUE_WORDS.search(text).start():]
                due = parse_due(after, self.clock()) or parse_due(text, self.clock())
                if due is None or due < self.clock() - 3600:
                    continue
                aid = self.add(clean_title(msg.get("subject", "")), due, "email",
                               f"email:{msg.get('from', '')}:{msg['id']}")
                if aid:
                    added.append(aid)
        self.store.kv_set("assign_scanned", self.clock())
        return added


def _when(ts: float) -> str:
    lt = time.localtime(ts)
    clock = f"{lt.tm_hour % 12 or 12}:{lt.tm_min:02d} {'AM' if lt.tm_hour < 12 else 'PM'}"
    return time.strftime("%a %d %b", lt) + ("" if (lt.tm_hour, lt.tm_min) == (23, 59) else f", {clock}")
