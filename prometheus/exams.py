"""Exam countdown planner: exam dates + syllabus → a day-by-day revision plan that keeps up with the owner.

Topics are taken from the syllabus (a PDF, notes or a typed list), spread over the days left with the last days
kept for revision, and study blocks go on the Google Calendar (asked once) or, without Google, the plan is posted
each day. Every evening Prometheus asks "did you finish today's topics?" in the Study chat; whatever wasn't done
moves to the next days that have room.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta

SCHEMA = """
CREATE TABLE IF NOT EXISTS exam_plans (id INTEGER PRIMARY KEY, exam TEXT NOT NULL, exam_date TEXT NOT NULL,
    created REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS plan_days (id INTEGER PRIMARY KEY, plan_id INTEGER NOT NULL, day TEXT NOT NULL,
    topics TEXT NOT NULL, kind TEXT NOT NULL DEFAULT 'study', done INTEGER NOT NULL DEFAULT 0);
"""
TOPICS_SCHEMA = {"type": "object", "properties": {"topics": {"type": "array", "items": {"type": "string"}}},
                 "required": ["topics"], "additionalProperties": False}
YES = re.compile(r"^\s*(yes|yeah|yep|done|finished|completed|all done|haan|ha|ho gaya|hogaya|kar liya)\b", re.I)
NO = re.compile(r"^\s*(no|nope|not yet|didn'?t|did not|nahi|nahin|none)\b", re.I)


def parse_date(text: str, today: date) -> date | None:
    t = (text or "").strip().lower()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d %B %Y", "%d %b %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return datetime.strptime(t.replace(",", ""), fmt).date()
        except ValueError:
            pass
    for fmt in ("%d %B", "%d %b", "%B %d", "%b %d", "%d/%m"):
        try:
            # a year is added for parsing (newer Python refuses dates without one); 2000 allows 29 Feb
            d = datetime.strptime(re.sub(r"(\d+)(st|nd|rd|th)", r"\1", t.replace(",", "")) + " 2000",
                                  fmt + " %Y").date()
            d = d.replace(year=today.year)
            return d if d >= today else d.replace(year=today.year + 1)
        except ValueError:
            pass
    m = re.search(r"in (\d+) (day|week)s?", t)
    if m:
        return today + timedelta(days=int(m.group(1)) * (7 if m.group(2) == "week" else 1))
    return None


def topics_from_text(text: str) -> list[str]:
    """A plain list: one topic per line, or numbered/bulleted, or separated by commas or semicolons."""
    lines = [re.sub(r"^\s*(?:[-*•]|\d+[.)]|unit \d+[:.-]?|chapter \d+[:.-]?)\s*", "", l, flags=re.I).strip()
             for l in (text or "").splitlines()]
    lines = [l for l in lines if 2 < len(l) < 120]
    if len(lines) >= 3:
        return lines[:80]
    parts = [p.strip() for p in re.split(r"[;,]", text or "") if 2 < len(p.strip()) < 120]
    return parts[:80]


def make_days(topics: list[str], start: date, exam: date, per_day: int = 0) -> list[tuple[date, list[str], str]]:
    """Spread topics over the days before the exam; the last days (about a fifth, at least 1) are for revision."""
    days = (exam - start).days
    if days <= 0 or not topics:
        return []
    revise = max(1, round(days / 5)) if days >= 3 else (1 if days == 2 else 0)
    study_days = max(1, days - revise)
    out = []
    per = per_day or -(-len(topics) // study_days)
    i = 0
    for n in range(study_days):
        chunk = topics[i:i + per]
        i += per
        if chunk:
            out.append((start + timedelta(days=n), chunk, "study"))
    for n in range(study_days, days):
        out.append((start + timedelta(days=n), ["Revise everything; practise past papers"], "revise"))
    return out


class Exams:
    def __init__(self, store, router=None, settings=None, clock=time.time):
        self.store, self.router, self.settings, self.clock = store, router, settings, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    def today(self) -> date:
        return datetime.fromtimestamp(self.clock()).date()

    def topics_from_syllabus(self, text: str) -> list[str]:
        simple = topics_from_text(text)
        if self.router is None or (len(simple) >= 3 and len(text) < 3000):
            return simple
        from prometheus.agents.base import UNTRUSTED_RULE, untrusted

        route = "chat" if getattr(self.settings, "study_cloud", False) else "private"
        try:
            reply = self.router.complete(route, [{"role": "user", "content": (
                "List the topics to revise from this syllabus, in the order taught, each short (a chapter or "
                "sub-topic, at most 8 words). At most 60.\n" + untrusted(text[:15000], "syllabus"))}],
                "You turn syllabuses into revision topic lists. " + UNTRUSTED_RULE, json_schema=TOPICS_SCHEMA,
                max_tokens=1500)
            got = [str(t).strip() for t in json.loads(reply.text).get("topics", []) if str(t).strip()]
            return got[:60] or simple
        except Exception:
            return simple

    def create(self, exam: str, exam_day: date, topics: list[str]) -> int:
        start = self.today() + timedelta(days=1) if datetime.fromtimestamp(self.clock()).hour >= 20 else self.today()
        days = make_days(topics, start, exam_day)
        if not days:
            raise ValueError("The exam date has to be after today, and there must be topics.")
        self.store._write("UPDATE exam_plans SET active = 0 WHERE lower(exam) = lower(?)", (exam,))
        pid = self.store._write("INSERT INTO exam_plans (exam, exam_date, created) VALUES (?, ?, ?)",
                                (exam[:80], exam_day.isoformat(), self.clock()))
        for d, chunk, kind in days:
            self.store._write("INSERT INTO plan_days (plan_id, day, topics, kind) VALUES (?, ?, ?, ?)",
                              (pid, d.isoformat(), json.dumps(chunk), kind))
        return pid

    def plans(self) -> list[dict]:
        return [dict(r) for r in self.store._rows("SELECT * FROM exam_plans WHERE active = 1 AND exam_date >= ? "
                                                  "ORDER BY exam_date", (self.today().isoformat(),))]

    def days(self, plan_id: int) -> list[dict]:
        rows = self.store._rows("SELECT * FROM plan_days WHERE plan_id = ? ORDER BY day, id", (plan_id,))
        return [{**dict(r), "topics": json.loads(r["topics"])} for r in rows]

    def for_day(self, day: date | None = None) -> list[dict]:
        day = day or self.today()
        out = []
        for p in self.plans():
            for d in self.days(p["id"]):
                if d["day"] == day.isoformat() and not d["done"]:
                    out.append({**d, "exam": p["exam"], "exam_date": p["exam_date"]})
        return out

    def show(self, plan_id: int) -> str:
        p = [x for x in self.plans() if x["id"] == plan_id]
        if not p:
            return "No such plan."
        left = (date.fromisoformat(p[0]["exam_date"]) - self.today()).days
        lines = [f"{p[0]['exam']}: {left} day(s) to go ({p[0]['exam_date']})"]
        for d in self.days(plan_id):
            mark = "✓ " if d["done"] else ""
            lines.append(f"- {mark}{date.fromisoformat(d['day']):%a %d %b}: {', '.join(d['topics'])}")
        return "\n".join(lines)

    def today_text(self) -> str:
        items = self.for_day()
        if not items:
            return ""
        return "\n".join(f"{i['exam']} ({(date.fromisoformat(i['exam_date']) - self.today()).days} days left): "
                         + ", ".join(i["topics"]) for i in items)

    def mark(self, day_id: int, done_topics: list[str] | None) -> str:
        """done_topics None = all done; [] = none; else the ones finished. The rest move to the next days."""
        rows = self.store._rows("SELECT * FROM plan_days WHERE id = ?", (day_id,))
        if not rows:
            return ""
        row = rows[0]
        topics = json.loads(row["topics"])
        missed = [] if done_topics is None else [t for t in topics if t not in done_topics]
        finished = [t for t in topics if t not in missed]
        self.store._write("UPDATE plan_days SET topics = ?, done = 1 WHERE id = ?",
                          (json.dumps(finished or ["(nothing finished)"]), day_id))
        if not missed:
            return "Great, today's topics are done. ✓"
        later = [d for d in self.days(row["plan_id"]) if d["day"] > row["day"] and not d["done"]]
        study = [d for d in later if d["kind"] == "study"] or later
        if not study:
            return f"Not done: {', '.join(missed)}. There are no days left before the exam, so fit them in when you can."
        for t in missed:
            target = min(study, key=lambda d: len(d["topics"]))
            target["topics"].append(t)
            self.store._write("UPDATE plan_days SET topics = ? WHERE id = ?", (json.dumps(target["topics"]),
                                                                              target["id"]))
        return (f"Moved {', '.join(missed)} to the next days. " +
                "Tomorrow: " + ", ".join(study[0]["topics"]) + ".")

    # -- the evening check-in ---------------------------------------------------------------
    def checkin_question(self) -> tuple[str, list[int]] | None:
        items = [i for i in self.for_day() if i["kind"] == "study"]
        if not items:
            return None
        topics = [t for i in items for t in i["topics"]]
        return (f"Did you finish today's topics? {', '.join(topics)}. Answer yes, no, or the ones you finished.",
                [i["id"] for i in items])

    def answer_checkin(self, chat_id: int, said: str) -> str | None:
        raw = self.store.kv_get("exam_checkin")
        if not raw:
            return None
        state = json.loads(raw)
        if state.get("chat") != chat_id or self.clock() - state.get("asked", 0) > 6 * 3600:
            return None
        self.store.kv_set("exam_checkin", "")
        replies = []
        low = said.lower()
        for day_id in state["days"]:
            rows = self.store._rows("SELECT topics FROM plan_days WHERE id = ?", (day_id,))
            topics = json.loads(rows[0]["topics"]) if rows else []
            if YES.match(said) and not re.search(r"\b(except|but not|apart from|only)\b", low):
                done = None
            elif NO.match(said) and not any(t.lower() in low for t in topics):
                done = []
            else:
                done = [t for t in topics if any(w in low for w in re.findall(r"[a-z]{4,}", t.lower()))]
                if re.search(r"\b(except|but not|apart from)\b", low):
                    done = [t for t in topics if t not in done]
            replies.append(self.mark(day_id, done))
        return " ".join(r for r in replies if r)
