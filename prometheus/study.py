"""Study coach: questions and flashcards from the owner's notes, quizzes in the chat or by voice, and spaced review.

Cards live in memory.db in decks ("physics", "chapter 3"). Each answer is marked (right, partly, wrong) and the
card comes back on a spaced schedule: wrong ones tomorrow (and again later in the same quiz), right ones after
longer and longer gaps (SM-2, the method Anki uses). Decks export as a file Anki can import.

Notes are the owner's files, so by default the cards are made and answers marked by the local AI; with
"study_cloud" on in Settings, Claude does it (better questions), sent only the notes chosen.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (id INTEGER PRIMARY KEY, deck TEXT NOT NULL, question TEXT NOT NULL,
    answer TEXT NOT NULL, source TEXT NOT NULL DEFAULT '', ease REAL NOT NULL DEFAULT 2.5,
    interval REAL NOT NULL DEFAULT 0, due REAL NOT NULL, reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS cards_due ON cards (due);
CREATE TABLE IF NOT EXISTS quiz (chat_id INTEGER PRIMARY KEY, deck TEXT NOT NULL, card_id INTEGER, asked INTEGER NOT NULL,
    right INTEGER NOT NULL, again TEXT NOT NULL DEFAULT '[]', started REAL NOT NULL);
"""
CHAT = "Study"
DAY = 86400
QUIZ_LENGTH = 10
MAKE_CHARS = 12_000
STOP = re.compile(r"^\s*(stop|end|quit|exit|done|enough|stop (the )?quiz|end (the )?quiz|that'?s enough|bas)\b", re.I)
SKIP = re.compile(r"^\s*(skip|pass|next|i don'?t know|dont know|no idea|pata nahi)\b", re.I)

CARD_SCHEMA = {"type": "object", "properties": {"cards": {"type": "array", "items": {
    "type": "object", "properties": {"question": {"type": "string"}, "answer": {"type": "string"}},
    "required": ["question", "answer"], "additionalProperties": False}}}, "required": ["cards"],
    "additionalProperties": False}
MARK_SCHEMA = {"type": "object", "properties": {"verdict": {"type": "string", "enum": ["right", "partly", "wrong"]},
                                                "feedback": {"type": "string"}},
               "required": ["verdict", "feedback"], "additionalProperties": False}


@dataclass
class Card:
    id: int
    deck: str
    question: str
    answer: str
    source: str
    ease: float
    interval: float
    due: float
    reps: int
    lapses: int


def deck_name(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s-]", " ", text or "").lower().split())[:60] or "general"


def schedule(card: Card, verdict: str, now: float) -> tuple[float, float, float, int, int]:
    """SM-2: (ease, interval days, due, reps, lapses) after an answer."""
    q = {"right": 5, "partly": 3, "wrong": 1}[verdict]
    ease = max(1.3, card.ease + 0.1 - (5 - q) * (0.08 + (5 - q) * 0.02))
    if q < 3:
        return ease, 1.0, now + DAY, 0, card.lapses + 1
    reps = card.reps + 1
    interval = 1.0 if reps == 1 else 3.0 if reps == 2 else round(card.interval * ease, 1)
    if q == 3:
        interval = max(1.0, interval * 0.6)
    return ease, interval, now + interval * DAY, reps, card.lapses


class Study:
    def __init__(self, store, router=None, settings=None, clock=time.time):
        self.store, self.router, self.settings, self.clock = store, router, settings, clock
        self.folder: Path | None = None        # where quiz PDFs go (Documents/Prometheus Study)
        with store._lock:
            store._db.executescript(SCHEMA)
            if "log" not in {r["name"] for r in store._db.execute("PRAGMA table_info(quiz)")}:
                store._db.execute("ALTER TABLE quiz ADD COLUMN log TEXT NOT NULL DEFAULT '[]'")
            store._db.commit()

    # -- cards ------------------------------------------------------------------------
    def _cards(self, where: str = "", args: tuple = ()) -> list[Card]:
        rows = self.store._rows(f"SELECT * FROM cards {where}", args)
        return [Card(r["id"], r["deck"], r["question"], r["answer"], r["source"], r["ease"], r["interval"], r["due"],
                     r["reps"], r["lapses"]) for r in rows]

    def add(self, deck: str, question: str, answer: str, source: str = "") -> int | None:
        deck, question, answer = deck_name(deck), " ".join(question.split())[:400], " ".join(answer.split())[:600]
        if not question or not answer:
            return None
        if self._cards("WHERE deck = ? AND lower(question) = lower(?)", (deck, question)):
            return None
        return self.store._write("INSERT INTO cards (deck, question, answer, source, due, created) VALUES "
                                 "(?, ?, ?, ?, ?, ?)", (deck, question, answer, source[:200], self.clock(), self.clock()))

    def decks(self) -> list[dict]:
        rows = self.store._rows("SELECT deck, COUNT(*) AS n, SUM(CASE WHEN due <= ? THEN 1 ELSE 0 END) AS due "
                                "FROM cards GROUP BY deck ORDER BY deck", (self.clock(),))
        return [dict(r) for r in rows]

    def due(self, deck: str = "", limit: int = 50) -> list[Card]:
        if deck:
            return self._cards("WHERE due <= ? AND deck = ? ORDER BY due LIMIT ?", (self.clock(), deck_name(deck), limit))
        return self._cards("WHERE due <= ? ORDER BY due LIMIT ?", (self.clock(), limit))

    def _route(self) -> str:
        return "chat" if getattr(self.settings, "study_cloud", False) else "private"

    def make(self, text: str, deck: str, source: str = "", count: int = 12) -> list[int]:
        """Ask the AI for question/answer cards from some notes."""
        from prometheus.agents.base import UNTRUSTED_RULE, untrusted

        text = (text or "").strip()[:MAKE_CHARS]
        if not text:
            return []
        prompt = (f"Make {count} flashcards from these study notes. Mix recall (definitions, facts, formulas), "
                  "understanding (why/how) and application (a short problem). Each answer short: one or two "
                  "sentences, exactly as the notes say it. Only what the notes contain.\n"
                  + untrusted(text, source or "notes"))
        reply = self.router.complete(self._route(), [{"role": "user", "content": prompt}],
                                     "You write clear flashcards for a student. " + UNTRUSTED_RULE,
                                     json_schema=CARD_SCHEMA, max_tokens=3000)
        try:
            cards = json.loads(reply.text).get("cards", [])
        except ValueError:
            return []
        ids = [self.add(deck, str(c.get("question", "")), str(c.get("answer", "")), source) for c in cards[:40]]
        return [i for i in ids if i]

    def mark(self, card: Card, said: str) -> tuple[str, str]:
        """(verdict, feedback). Quick check first; the AI only when it isn't clear."""
        a, b = _words(card.answer), _words(said)
        if not b:
            return "wrong", f"The answer: {card.answer}"
        overlap = len(a & b) / max(1, len(a))
        if _norm(said) == _norm(card.answer) or overlap >= 0.8:
            return "right", f"Right. {card.answer}"
        if self.router is None:
            return ("partly" if overlap >= 0.4 else "wrong"), f"The answer: {card.answer}"
        prompt = (f"Question: {card.question}\nCorrect answer: {card.answer}\nStudent's answer: {said}\n"
                  "Is the student's answer right (same meaning), partly right, or wrong? Feedback in one short "
                  "sentence that gives the correct answer if they missed it.")
        try:
            reply = self.router.complete(self._route(), [{"role": "user", "content": prompt}],
                                         "You mark a student's flashcard answers kindly and exactly.",
                                         json_schema=MARK_SCHEMA, max_tokens=200)
            data = json.loads(reply.text)
            return data["verdict"], data["feedback"]
        except Exception:
            return ("partly" if overlap >= 0.4 else "wrong"), f"The answer: {card.answer}"

    def grade(self, card: Card, verdict: str) -> None:
        ease, interval, due, reps, lapses = schedule(card, verdict, self.clock())
        self.store._write("UPDATE cards SET ease = ?, interval = ?, due = ?, reps = ?, lapses = ? WHERE id = ?",
                          (ease, interval, due, reps, lapses, card.id))

    def export_anki(self, deck: str, folder: Path) -> Path:
        """A tab-separated file: Anki → File → Import picks it up as front/back cards."""
        cards = self._cards("WHERE deck = ? ORDER BY id", (deck_name(deck),))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{deck_name(deck)} (Anki).txt"
        lines = ["#separator:tab", "#html:false", f"#deck:{deck_name(deck)}"]
        lines += [f"{c.question.replace(chr(9), ' ')}\t{c.answer.replace(chr(9), ' ')}" for c in cards]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    # -- a quiz in a chat ---------------------------------------------------------------------
    def quiz_state(self, chat_id: int) -> dict | None:
        rows = self.store._rows("SELECT * FROM quiz WHERE chat_id = ?", (chat_id,))
        return dict(rows[0]) if rows else None

    def start_quiz(self, chat_id: int, deck: str = "") -> str:
        cards = self.due(deck, QUIZ_LENGTH)
        if not cards:
            pool = self._cards("WHERE deck = ? ORDER BY due LIMIT ?", (deck_name(deck), QUIZ_LENGTH)) if deck else []
            if not pool:
                names = ", ".join(d["deck"] for d in self.decks()) or "none yet"
                return (f"Nothing is due{' in ' + deck if deck else ''}. Your decks: {names}. "
                        "Say \"make flashcards from <file>\" to add some.")
            cards = pool
        order = [c.id for c in cards]
        self.store._write("INSERT OR REPLACE INTO quiz (chat_id, deck, card_id, asked, right, again, started) "
                          "VALUES (?, ?, ?, 0, 0, ?, ?)", (chat_id, deck_name(deck) if deck else "", order[0],
                                                           json.dumps(order[1:]), self.clock()))
        first = self._cards("WHERE id = ?", (order[0],))[0]
        return f"Quiz: {len(order)} card(s). Say \"stop\" any time.\n\nQ1. {first.question}"

    def answer(self, chat_id: int, said: str) -> str | None:
        """The owner's reply during a quiz: mark it and ask the next one. None when no quiz is running."""
        state = self.quiz_state(chat_id)
        if state is None:
            return None
        if STOP.match(said):
            return self._finish(state)
        cards = self._cards("WHERE id = ?", (state["card_id"],))
        rest = json.loads(state["again"])
        if not cards:
            return self._finish(state)
        card = cards[0]
        if SKIP.match(said):
            verdict, feedback = "wrong", f"The answer: {card.answer}"
        else:
            verdict, feedback = self.mark(card, said)
        self.grade(card, verdict)
        log = json.loads(state.get("log") or "[]")
        log.append({"q": card.question, "said": said.strip()[:500], "verdict": verdict, "answer": card.answer,
                    "feedback": feedback})
        self.store._write("UPDATE quiz SET log = ? WHERE chat_id = ?", (json.dumps(log), chat_id))
        state["log"] = json.dumps(log)
        right = state["right"] + (1 if verdict == "right" else 0)
        asked = state["asked"] + 1
        if verdict == "wrong" and rest.count(card.id) == 0 and asked < QUIZ_LENGTH * 2:
            rest.append(card.id)                       # a wrong card comes back later in this quiz
        mark = {"right": "✓", "partly": "≈", "wrong": "✗"}[verdict]
        if not rest:
            self.store._write("UPDATE quiz SET asked = ?, right = ? WHERE chat_id = ?", (asked, right, chat_id))
            return f"{mark} {feedback}\n\n" + self._finish({**state, "asked": asked, "right": right})
        nxt = self._cards("WHERE id = ?", (rest[0],))[0]
        self.store._write("UPDATE quiz SET card_id = ?, again = ?, asked = ?, right = ? WHERE chat_id = ?",
                          (nxt.id, json.dumps(rest[1:]), asked, right, chat_id))
        return f"{mark} {feedback}\n\nQ{asked + 1}. {nxt.question}"

    def quiz_pdf(self, state: dict) -> Path | None:
        """The quiz as a PDF: each question, what was said, the mark and the right answer."""
        log = json.loads(state.get("log") or "[]")
        if not log or self.folder is None:
            return None
        from prometheus.actions.pdf import markdown_pdf

        deck = state.get("deck") or "everything due"
        when = time.strftime("%Y-%m-%d %H.%M", time.localtime(self.clock()))
        right = sum(1 for e in log if e["verdict"] == "right")
        lines = [f"# Quiz: {deck}", f"{time.strftime('%A %d %B %Y, %H:%M', time.localtime(self.clock()))} · "
                                    f"score {right} of {len(log)}", ""]
        for i, e in enumerate(log, 1):
            mark = {"right": "Right", "partly": "Partly right", "wrong": "Wrong"}[e["verdict"]]
            lines += [f"## {i}. {e['q']}", f"- **Your answer:** {e['said'] or '(skipped)'}", f"- **Mark:** {mark}",
                      f"- **Answer:** {e['answer']}", ""]
        missed = [e for e in log if e["verdict"] != "right"]
        if missed:
            lines += ["## To revise", *[f"- {e['q']}: {e['answer']}" for e in missed]]
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / f"Quiz {deck_name(deck)} {when}.pdf"
        path.write_bytes(markdown_pdf("\n".join(lines), f"Quiz: {deck}"))
        task = self.store.latest_task(state["chat_id"])
        if task:
            self.store.add_output(int(task["id"]), str(path), "created")
        return path

    def _finish(self, state: dict) -> str:
        try:
            pdf = self.quiz_pdf(state)
        except Exception:
            pdf = None
        self.store._write("DELETE FROM quiz WHERE chat_id = ?", (state["chat_id"],))
        self.store.kv_set("study_last", json.dumps({"when": self.clock(), "asked": state["asked"],
                                                    "right": state["right"]}))
        due = len(self.due())
        return (f"Quiz done: {state['right']} of {state['asked']} right." +
                (f" {due} card(s) still due." if due else " Nothing else is due today.") +
                (f" The questions and answers are saved as {pdf.name} (in Outputs)." if pdf else ""))

    def summary_today(self) -> str:
        due = len(self.due())
        try:
            last = json.loads(self.store.kv_get("study_last") or "{}")
        except ValueError:
            last = {}
        bits = []
        if last and self.clock() - last.get("when", 0) < DAY:
            bits.append(f"quiz score {last['right']} of {last['asked']}")
        if due:
            bits.append(f"{due} flashcards due")
        return ", ".join(bits)


def _norm(s: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", (s or "").lower()))


STOPWORDS = set("a an the of to in is are was were and or it its that this for on with as by be at".split())


def _words(s: str) -> set[str]:
    return {w for w in _norm(s).split() if w not in STOPWORDS}
