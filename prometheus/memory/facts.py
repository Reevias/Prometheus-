"""Facts about the owner and the people, places and things in their life, linked together.

Each fact is (subject, attribute, value): ("Ravi", "relation", "brother"), ("Ravi", "lives in", "Pune"),
("me", "phone", "98…"). A value that names another subject links the two, so a question about "my brother"
brings up everything known about Ravi. When a fact changes (a new phone number), the old one is kept with the date
it stopped being true. Every night a tidy-up merges duplicates and closes facts whose date has passed; each change
is listed on the Memory page with Undo.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (id INTEGER PRIMARY KEY, subject TEXT NOT NULL, attribute TEXT NOT NULL,
    value TEXT NOT NULL, created REAL NOT NULL, until REAL, source TEXT NOT NULL DEFAULT '', replaced_by INTEGER);
CREATE INDEX IF NOT EXISTS facts_subject ON facts (subject);
CREATE TABLE IF NOT EXISTS fact_changes (id INTEGER PRIMARY KEY, created REAL NOT NULL, text TEXT NOT NULL,
    undo TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0);
"""
OWNER = "me"
OWNER_WORDS = {"me", "i", "myself", "owner", "user", "the user", "the owner"}
# attributes that can hold several values at once (everything else has one current value)
MANY = {"likes", "dislikes", "hobby", "hobbies", "friend", "friends", "allergy", "allergies", "studies", "subject",
        "subjects", "language", "languages", "pet", "pets", "child", "children", "sibling", "siblings", "skill",
        "skills", "goal", "goals", "favourite", "favorite", "interest", "interests", "knows", "works with"}
DATED = re.compile(r"\b(exam|test|trip|appointment|deadline|interview|meeting|flight|travel|plan|event|due)\b", re.I)
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"], 1)}
SECRET = re.compile(r"pass(word|phrase)|passwd|secret|api[ _-]?key|token|\bpin\b|\botp\b|cvv|card number|"
                    r"sk-[A-Za-z0-9_-]{6,}|ghp_|\b\d{12,19}\b", re.I)
PROMPT_FACTS = 25


@dataclass
class Fact:
    id: int
    subject: str
    attribute: str
    value: str
    created: float
    until: float | None
    source: str

    def sentence(self) -> str:
        who = "You" if self.subject == OWNER else self.subject
        if self.attribute == "relation":
            return f"{who} is your {self.value}" if self.subject != OWNER else f"You are {self.value}"
        return f"{who}: {self.attribute} = {self.value}"


def clean_subject(s: str) -> str:
    s = " ".join(str(s or "").split()).strip(" .")
    if s.lower() in OWNER_WORDS:
        return OWNER
    return s[:80]


def _norm(s: str) -> str:
    return " ".join(str(s or "").lower().split()).strip(" .")


class Facts:
    def __init__(self, store, clock=time.time):
        self.store, self.clock = store, clock
        with store._lock:
            store._db.executescript(SCHEMA)
            store._db.commit()

    # -- reading ---------------------------------------------------------------------------
    def _facts(self, where: str = "", args: tuple = ()) -> list[Fact]:
        rows = self.store._rows(f"SELECT * FROM facts {where} ORDER BY created, id", args)
        return [Fact(r["id"], r["subject"], r["attribute"], r["value"], r["created"], r["until"], r["source"])
                for r in rows]

    def current(self) -> list[Fact]:
        return self._facts("WHERE until IS NULL")

    def all(self) -> list[Fact]:
        return self._facts()

    def get(self, fact_id: int) -> Fact | None:
        got = self._facts("WHERE id = ?", (fact_id,))
        return got[0] if got else None

    def subjects(self) -> list[str]:
        return sorted({f.subject for f in self.current()}, key=lambda s: (s != OWNER, s.lower()))

    def about(self, subject: str, past: bool = True) -> list[Fact]:
        return self._facts("WHERE lower(subject) = lower(?)" + ("" if past else " AND until IS NULL"), (subject,))

    def relevant(self, text: str, limit: int = PROMPT_FACTS) -> list[str]:
        """Lines for the planner: facts about whoever the request mentions, one link further, and the owner."""
        words = set(re.findall(r"[a-z0-9']+", (text or "").lower()))
        current = self.current()
        hit = set()
        for f in current:
            names = set(re.findall(r"[a-z0-9']+", f.subject.lower()))
            vals = set(re.findall(r"[a-z0-9']+", f.value.lower()))
            if names & words or (f.attribute == "relation" and vals & words):
                hit.add(f.subject)
        subjects = {s.lower() for s in hit}
        for f in current:                       # one link further: a value that names another subject
            if f.subject.lower() in subjects and f.value.lower() in {c.subject.lower() for c in current}:
                hit.add(next(c.subject for c in current if c.subject.lower() == f.value.lower()))
        chosen = [f for f in current if f.subject in hit] + [f for f in current if f.subject == OWNER
                                                             and f.subject not in hit]
        lines = []
        for f in chosen[:limit]:
            line = f.sentence()
            old = [o for o in self._facts("WHERE replaced_by = ?", (f.id,))]
            if old:
                line += f" (before {time.strftime('%d %b %Y', time.localtime(old[-1].until or 0))}: {old[-1].value})"
            lines.append(line)
        return lines

    # -- saving ---------------------------------------------------------------------------
    def save(self, subject: str, attribute: str, value: str, source: str = "") -> tuple[int | None, str]:
        """Save one fact. Returns (id, what to tell the owner). A changed single fact keeps the old value, dated."""
        subject, attribute = clean_subject(subject), _norm(attribute)[:60]
        value = " ".join(str(value or "").split())[:200]
        if not (subject and attribute and value) or SECRET.search(f"{attribute} {value}"):
            return None, ""
        same = [f for f in self.current() if f.subject.lower() == subject.lower() and f.attribute == attribute]
        if any(_norm(f.value) == _norm(value) for f in same):
            return None, ""                                    # already known
        now = self.clock()
        known = next((f.subject for f in self.current() if f.subject.lower() == subject.lower()), subject)
        new_id = self.store._write("INSERT INTO facts (subject, attribute, value, created, source) VALUES "
                                   "(?, ?, ?, ?, ?)", (known, attribute, value, now, source))
        fact = self.get(new_id)
        if same and attribute not in MANY:
            for old in same:
                self.store._write("UPDATE facts SET until = ?, replaced_by = ? WHERE id = ?", (now, new_id, old.id))
            return new_id, f"{fact.sentence()} (was {same[-1].value})"
        return new_id, fact.sentence()

    def forget(self, fact_id: int) -> None:
        """Undo a saved fact: delete it and make the value it replaced current again."""
        self.store._write("UPDATE facts SET until = NULL, replaced_by = NULL WHERE replaced_by = ?", (fact_id,))
        self.store._write("DELETE FROM facts WHERE id = ?", (fact_id,))

    # -- the nightly tidy-up ------------------------------------------------------------------
    def _log(self, text: str, undo: list) -> None:
        self.store._write("INSERT INTO fact_changes (created, text, undo) VALUES (?, ?, ?)",
                          (self.clock(), text, json.dumps(undo)))

    def changes(self, since_days: float = 3) -> list[dict]:
        rows = self.store._rows("SELECT * FROM fact_changes WHERE created > ? AND undone = 0 ORDER BY id DESC",
                                (self.clock() - since_days * 86400,))
        return [dict(r) for r in rows]

    def undo_change(self, change_id: int) -> bool:
        rows = self.store._rows("SELECT undo FROM fact_changes WHERE id = ? AND undone = 0", (change_id,))
        if not rows:
            return False
        for op in json.loads(rows[0]["undo"]):
            if op[0] == "restore":                 # a fact row as it was
                f = op[1]
                self.store._write("INSERT OR REPLACE INTO facts (id, subject, attribute, value, created, until, "
                                  "source, replaced_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                  (f["id"], f["subject"], f["attribute"], f["value"], f["created"], f["until"],
                                   f["source"], f.get("replaced_by")))
            elif op[0] == "memory":
                self.store.add_memory(op[1], source=op[2])
        self.store._write("UPDATE fact_changes SET undone = 1 WHERE id = ?", (change_id,))
        return True

    def _row(self, fact_id: int) -> dict:
        return dict(self.store._rows("SELECT * FROM facts WHERE id = ?", (fact_id,))[0])

    def _date_passed(self, value: str, set_on: float) -> bool:
        """'12 March', '2026-11-03', '3/11' → True when that day is over (the year guessed from when it was said)."""
        now, v = self.clock(), value.lower()
        m = re.search(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", v)
        if m:
            day = time.mktime((int(m[1]), int(m[2]), int(m[3]), 23, 59, 0, 0, 0, -1))
            return day < now
        m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", v) \
            or re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+(\d{1,2})\b", v)
        if not m:
            return False
        a, b = m.group(1), m.group(2)
        day_n, mon = (int(a), MONTHS[b[:3]]) if a.isdigit() else (int(b), MONTHS[a[:3]])
        year = time.localtime(set_on).tm_year
        when = time.mktime((year, mon, day_n, 23, 59, 0, 0, 0, -1))
        if when < set_on - 86400:                  # said in December about "5 Jan": next year
            when = time.mktime((year + 1, mon, day_n, 23, 59, 0, 0, 0, -1))
        return when < now

    def tidy(self) -> list[str]:
        """Merge duplicates, settle double values and close events whose date has passed. Returns what changed."""
        done = []
        current = self.current()
        # 1. the same subject spelled differently ("ravi" and "Ravi"): use the first spelling
        first: dict[str, str] = {}
        for f in current:
            first.setdefault(f.subject.lower(), f.subject)
        for f in current:
            if f.subject != first[f.subject.lower()]:
                undo = [("restore", self._row(f.id))]
                self.store._write("UPDATE facts SET subject = ? WHERE id = ?", (first[f.subject.lower()], f.id))
                self._log(f"Merged '{f.subject}' into '{first[f.subject.lower()]}'", undo)
                done.append(f"merged {f.subject}")
        # 2. the same fact twice: keep the oldest
        seen: dict[tuple, int] = {}
        for f in self.current():
            key = (f.subject.lower(), f.attribute, _norm(f.value))
            if key in seen:
                undo = [("restore", self._row(f.id))]
                self.store._write("DELETE FROM facts WHERE id = ?", (f.id,))
                self._log(f"Removed a duplicate: {f.sentence()}", undo)
                done.append("duplicate")
            else:
                seen[key] = f.id
        # 3. two current values where there should be one: the newer wins, the older is kept as past
        groups: dict[tuple, list[Fact]] = {}
        for f in self.current():
            if f.attribute not in MANY:
                groups.setdefault((f.subject.lower(), f.attribute), []).append(f)
        for facts in groups.values():
            for old in facts[:-1]:
                undo = [("restore", self._row(old.id))]
                self.store._write("UPDATE facts SET until = ?, replaced_by = ? WHERE id = ?",
                                  (facts[-1].created, facts[-1].id, old.id))
                self._log(f"Kept the newer {old.attribute} for {old.subject}: {facts[-1].value} "
                          f"(the older one, {old.value}, is kept as past)", undo)
                done.append("settled")
        # 4. events whose date is over
        for f in self.current():
            if DATED.search(f.attribute + " " + f.value) and self._date_passed(f.value, f.created):
                undo = [("restore", self._row(f.id))]
                self.store._write("UPDATE facts SET until = ? WHERE id = ?", (self.clock(), f.id))
                self._log(f"Marked as past (its date is over): {f.sentence()}", undo)
                done.append("past")
        # 5. plain memories saved twice
        texts: dict[str, int] = {}
        for m in self.store.memories():
            key = _norm(m.text)
            if key in texts:
                self.store.delete_memory(m.id)
                self._log(f"Removed a repeated memory: {m.text[:120]}", [("memory", m.text, m.source)])
                done.append("memory")
            else:
                texts[key] = m.id
        return done
