"""One SQLite file shared by every agent. All writes go through one lock."""
from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from array import array
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (id INTEGER PRIMARY KEY, title TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chat_notes (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, text TEXT NOT NULL,
    created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS tasks (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, text TEXT NOT NULL,
    status TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS steps (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, agent TEXT NOT NULL,
    goal TEXT NOT NULL, inputs TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS agent_notes (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL, agent TEXT NOT NULL,
    text TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY, actor TEXT NOT NULL, action TEXT NOT NULL,
    detail TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS approvals (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, summary TEXT NOT NULL,
    payload TEXT NOT NULL, status TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS costs (id INTEGER PRIMARY KEY, provider TEXT NOT NULL, model TEXT NOT NULL,
    usd REAL NOT NULL, tokens_in INTEGER NOT NULL, tokens_out INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS documents (id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
    source TEXT NOT NULL, private INTEGER NOT NULL, hash TEXT NOT NULL, chars INTEGER NOT NULL,
    created REAL NOT NULL, updated REAL NOT NULL);
CREATE TABLE IF NOT EXISTS chunks (id INTEGER PRIMARY KEY, doc_id INTEGER NOT NULL, parent_id INTEGER,
    seq INTEGER NOT NULL, text TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks (doc_id);
CREATE TABLE IF NOT EXISTS chat_segments (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, topic TEXT NOT NULL,
    first_msg INTEGER NOT NULL, last_msg INTEGER NOT NULL, doc_id INTEGER NOT NULL, started REAL NOT NULL);
CREATE INDEX IF NOT EXISTS chat_segments_topic ON chat_segments (topic);
CREATE TABLE IF NOT EXISTS outputs (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, path TEXT NOT NULL,
    kind TEXT NOT NULL, is_dir INTEGER NOT NULL, created REAL NOT NULL);
CREATE INDEX IF NOT EXISTS outputs_chat ON outputs (chat_id);
CREATE TABLE IF NOT EXISTS reminders (id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL, text TEXT NOT NULL,
    due REAL NOT NULL, repeat TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'waiting', created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (message_id INTEGER PRIMARY KEY, rating INTEGER NOT NULL, note TEXT NOT NULL,
    created REAL NOT NULL, learned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS allow_rules (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL,
    label TEXT NOT NULL, created REAL NOT NULL, UNIQUE (kind, key));
CREATE TABLE IF NOT EXISTS lessons (id INTEGER PRIMARY KEY, text TEXT NOT NULL, worked INTEGER NOT NULL,
    created REAL NOT NULL, learned INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chunk_vectors (chunk_id INTEGER PRIMARY KEY, model TEXT NOT NULL, vec BLOB NOT NULL);
"""

# The search index matches word forms ("taxes" finds "tax") through the porter stemmer.
INDEX_SQL = ("CREATE VIRTUAL TABLE search_index USING fts5(kind UNINDEXED, ref_id UNINDEXED, body, "
             "tokenize='porter unicode61')")
INDEX_VERSION = 3
STOPWORDS = frozenset("a an and are as at be by for from has have i in is it me my of on or that the this to was "
                      "what when where which who will with you your do does did can could should would please "
                      "about into than then them they their there here just some any".split())
KIND_WEIGHT = {"memory": 1.5, "chat_note": 1.2, "agent_note": 1.0, "message": 0.8, "chunk": 1.0}
MARK_START, MARK_END = "\ue000", "\ue001"
RRF_K = 60                 # reciprocal rank fusion: how much a top rank counts over a lower one
EMBED_BATCH = 16
RECENCY_DAYS = 30.0


@dataclass
class Message:
    id: int
    chat_id: int
    role: str
    text: str
    created: float
    model: str = ""
    private: bool = False    # the text holds details from the owner's files: never sent to a cloud model
    topic: str = ""          # what this part of the chat is about ("" = same as the message before)


@dataclass
class Note:
    id: int
    text: str
    created: float
    source: str = ""


@dataclass
class Recalled:
    kind: str        # memory | chat_note | agent_note | message
    ref_id: int
    text: str
    source: str
    created: float
    score: float


@dataclass
class Document:
    id: int
    key: str
    title: str
    source: str
    private: bool
    chars: int
    chunks: int
    updated: float


@dataclass
class ChunkHit:
    doc_id: int
    title: str
    source: str
    private: bool
    match: str       # the small chunk that matched
    context: str     # the larger parent chunk around it, for the model to read
    score: float


@dataclass
class Step:
    id: int
    task_id: int
    agent: str
    goal: str
    inputs: dict
    status: str
    result: str
    data: dict = field(default_factory=dict)
    created: float = 0.0


@dataclass
class Approval:
    id: int
    kind: str
    summary: str
    payload: dict
    status: str


@dataclass
class SearchHit:
    kind: str
    ref_id: int
    snippet: str


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self.listeners = []    # called with (chat_id) after an answer is saved, e.g. the chat archive
        self.embedder = None   # optional: turns text into vectors for meaning-based search (local Ollama only)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            columns = {r["name"] for r in self._db.execute("PRAGMA table_info(messages)")}
            if "model" not in columns:  # databases made before v1.1
                self._db.execute("ALTER TABLE messages ADD COLUMN model TEXT NOT NULL DEFAULT ''")
            if "summary" not in {r["name"] for r in self._db.execute("PRAGMA table_info(chats)")}:
                self._db.execute("ALTER TABLE chats ADD COLUMN summary TEXT NOT NULL DEFAULT ''")
                self._db.execute("ALTER TABLE chats ADD COLUMN summary_upto INTEGER NOT NULL DEFAULT 0")
            if "kind" not in {r["name"] for r in self._db.execute("PRAGMA table_info(reminders)")}:
                self._db.execute("ALTER TABLE reminders ADD COLUMN kind TEXT NOT NULL DEFAULT 'reminder'")
            approval_cols = {r["name"] for r in self._db.execute("PRAGMA table_info(approvals)")}
            if "inline" not in approval_cols:
                self._db.execute("ALTER TABLE approvals ADD COLUMN chat_id INTEGER NOT NULL DEFAULT 0")
                self._db.execute("ALTER TABLE approvals ADD COLUMN inline INTEGER NOT NULL DEFAULT 0")
            if "named_by" not in {r["name"] for r in self._db.execute("PRAGMA table_info(chats)")}:
                self._db.execute("ALTER TABLE chats ADD COLUMN named_by TEXT NOT NULL DEFAULT 'auto'")
            if "private" not in {r["name"] for r in self._db.execute("PRAGMA table_info(messages)")}:
                self._db.execute("ALTER TABLE messages ADD COLUMN private INTEGER NOT NULL DEFAULT 0")
                # Answers saved before this existed may hold details from personal files: treat them all as private.
                self._db.execute("UPDATE messages SET private = 1 WHERE role = 'assistant'")
            if "finished" not in {r["name"] for r in self._db.execute("PRAGMA table_info(tasks)")}:
                self._db.execute("ALTER TABLE tasks ADD COLUMN finished REAL NOT NULL DEFAULT 0")
            if "topic" not in {r["name"] for r in self._db.execute("PRAGMA table_info(messages)")}:
                self._db.execute("ALTER TABLE messages ADD COLUMN topic TEXT NOT NULL DEFAULT ''")
            if "chat_id" not in {r["name"] for r in self._db.execute("PRAGMA table_info(costs)")}:
                self._db.execute("ALTER TABLE costs ADD COLUMN chat_id INTEGER NOT NULL DEFAULT 0")
            columns = {r["name"] for r in self._db.execute("PRAGMA table_info(memories)")}
            if "source" not in columns:  # databases made before v1.2
                self._db.execute("ALTER TABLE memories ADD COLUMN source TEXT NOT NULL DEFAULT ''")
            if self._db.execute("PRAGMA user_version").fetchone()[0] < INDEX_VERSION:
                self._rebuild_index()
            self._db.commit()

    def _rebuild_index(self) -> None:
        """Recreate the search index (new word-form matching) and refill it from the real tables."""
        self._db.execute("DROP TABLE IF EXISTS search_index")
        self._db.execute(INDEX_SQL)
        for kind, table in (("memory", "memories"), ("chat_note", "chat_notes"), ("agent_note", "agent_notes"),
                            ("message", "messages")):
            self._db.execute(f"INSERT INTO search_index (kind, ref_id, body) SELECT ?, id, text FROM {table}",
                             (kind,))
        self._db.execute("INSERT INTO search_index (kind, ref_id, body) "
                         "SELECT 'chunk', id, text FROM chunks WHERE parent_id IS NOT NULL")
        self._db.execute(f"PRAGMA user_version = {INDEX_VERSION}")

    # -- helpers ---------------------------------------------------------
    def _write(self, sql: str, args: tuple = ()) -> int:
        with self._lock:
            cur = self._db.execute(sql, args)
            self._db.commit()
            return cur.lastrowid

    def _rows(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _index(self, kind: str, ref_id: int, body: str) -> None:
        self._write("INSERT INTO search_index (kind, ref_id, body) VALUES (?, ?, ?)", (kind, ref_id, body))

    def _unindex(self, kind: str, ref_id: int | None = None) -> None:
        if ref_id is None:
            self._write("DELETE FROM search_index WHERE kind = ?", (kind,))
        else:
            self._write("DELETE FROM search_index WHERE kind = ? AND ref_id = ?", (kind, ref_id))

    # -- chats -----------------------------------------------------------
    def create_chat(self, title: str) -> int:
        return self._write("INSERT INTO chats (title, created) VALUES (?, ?)", (title, time.time()))

    def get_summary(self, chat_id: int) -> tuple[str, int]:
        rows = self._rows("SELECT summary, summary_upto FROM chats WHERE id = ?", (chat_id,))
        return (rows[0]["summary"], rows[0]["summary_upto"]) if rows else ("", 0)

    def set_summary(self, chat_id: int, text: str, upto: int) -> None:
        self._write("UPDATE chats SET summary = ?, summary_upto = ? WHERE id = ?", (text, upto, chat_id))

    def rename_chat(self, chat_id: int, title: str, by: str = "auto") -> None:
        """by='user' marks a name the owner chose: automatic naming never changes it again."""
        self._write("UPDATE chats SET title = ?, named_by = ? WHERE id = ?", (title, by, chat_id))

    def chat_named_by(self, chat_id: int) -> str:
        rows = self._rows("SELECT named_by FROM chats WHERE id = ?", (chat_id,))
        return rows[0]["named_by"] if rows else "auto"

    # -- reminders -------------------------------------------------------------------------
    def add_reminder(self, chat_id: int, text: str, due: float, repeat: str = "", kind: str = "reminder") -> int:
        """kind: 'reminder' (ring and say it) or 'goal' (run it as a task at that time)."""
        return self._write("INSERT INTO reminders (chat_id, text, due, repeat, created, kind) VALUES (?, ?, ?, ?, ?, ?)",
                           (chat_id, text, due, repeat, time.time(), kind))

    def reminders(self, chat_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM reminders WHERE state = 'waiting'" + (" AND chat_id = ?" if chat_id is not None else "")
        return [dict(r) for r in self._rows(sql + " ORDER BY due", (chat_id,) if chat_id is not None else ())]

    def reminders_due(self, now: float) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT * FROM reminders WHERE state = 'waiting' AND due <= ? "
                                            "ORDER BY due", (now,))]

    def reminder_fired(self, reminder_id: int, next_due: float | None) -> None:
        if next_due is None:
            self._write("UPDATE reminders SET state = 'done' WHERE id = ?", (reminder_id,))
        else:
            self._write("UPDATE reminders SET due = ? WHERE id = ?", (next_due, reminder_id))

    def cancel_reminder(self, reminder_id: int) -> bool:
        rows = self._rows("SELECT id FROM reminders WHERE id = ? AND state = 'waiting'", (reminder_id,))
        if rows:
            self._write("UPDATE reminders SET state = 'cancelled' WHERE id = ?", (reminder_id,))
        return bool(rows)

    # -- feedback on answers: what Prometheus learns from -------------------------------------
    def rate(self, message_id: int, rating: int, note: str = "") -> None:
        self._write("INSERT OR REPLACE INTO feedback (message_id, rating, note, created, learned) VALUES (?, ?, ?, ?, 0)",
                    (message_id, 1 if rating > 0 else -1, note[:500], time.time()))

    def rating(self, message_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM feedback WHERE message_id = ?", (message_id,))
        return dict(rows[0]) if rows else None

    def ratings_for(self, chat_id: int) -> dict[int, dict]:
        rows = self._rows("SELECT f.* FROM feedback f JOIN messages m ON m.id = f.message_id WHERE m.chat_id = ?",
                          (chat_id,))
        return {int(r["message_id"]): dict(r) for r in rows}

    def feedback_to_learn(self, limit: int = 40) -> list[dict]:
        """New ratings with the question and the answer (never answers holding private file details)."""
        rows = self._rows(
            "SELECT f.*, m.text AS answer, m.chat_id, m.private, "
            "(SELECT u.text FROM messages u WHERE u.chat_id = m.chat_id AND u.id < m.id AND u.role = 'user' "
            " ORDER BY u.id DESC LIMIT 1) AS question FROM feedback f JOIN messages m ON m.id = f.message_id "
            "WHERE f.learned = 0 AND m.private = 0 ORDER BY f.created DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    def mark_learned(self, message_ids: list[int]) -> None:
        with self._lock:
            self._db.executemany("UPDATE feedback SET learned = 1 WHERE message_id = ?", [(i,) for i in message_ids])
            self._db.commit()

    def message(self, message_id: int):
        rows = self._rows("SELECT * FROM messages WHERE id = ?", (message_id,))
        if not rows:
            return None
        r = rows[0]
        return Message(r["id"], r["chat_id"], r["role"], r["text"], r["created"], r["model"], bool(r["private"]),
                       r["topic"])

    # -- lessons from tasks: what failed and what worked --------------------------------------
    def add_lesson(self, text: str, worked: bool) -> int:
        text = " ".join(text.split())[:400]
        rows = self._rows("SELECT id FROM lessons WHERE text = ?", (text,))
        if rows:
            return int(rows[0]["id"])
        return self._write("INSERT INTO lessons (text, worked, created) VALUES (?, ?, ?)",
                           (text, int(worked), time.time()))

    def lessons(self, limit: int = 100) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT * FROM lessons ORDER BY created DESC LIMIT ?", (limit,))]

    def relevant_lessons(self, request: str, limit: int = 4) -> list[str]:
        words = {w for w in self._terms(request) if len(w) > 2}
        if not words:
            return []
        scored = []
        for r in self.lessons(300):
            low = r["text"].lower()
            hits = sum(1 for w in words if w in low)
            if hits:
                scored.append((hits, r["created"], r["text"]))
        scored.sort(reverse=True)
        return [t for _, _, t in scored[:limit]]

    def lessons_to_learn(self) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT * FROM lessons WHERE learned = 0 ORDER BY created LIMIT 40")]

    def mark_lessons_learned(self, ids: list[int]) -> None:
        with self._lock:
            self._db.executemany("UPDATE lessons SET learned = 1 WHERE id = ?", [(i,) for i in ids])
            self._db.commit()

    def delete_lesson(self, lesson_id: int) -> None:
        self._write("DELETE FROM lessons WHERE id = ?", (lesson_id,))

    # -- small saved values (last daily run, repositories already suggested…) ----------------------
    def kv_get(self, key: str, default=None):
        rows = self._rows("SELECT value FROM kv WHERE key = ?", (key,))
        return json.loads(rows[0]["value"]) if rows else default

    def kv_set(self, key: str, value) -> None:
        self._write("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # -- outputs: files and folders a chat created or found, shown under the chat ---------------
    def add_output(self, task_id: int, path: str, kind: str, is_dir: bool = False) -> None:
        """kind: 'created' or 'found'. The same path shows once per chat (the newest kind wins)."""
        rows = self._rows("SELECT chat_id FROM tasks WHERE id = ?", (task_id,))
        if not rows:
            return
        chat_id = int(rows[0]["chat_id"])
        with self._lock:
            self._db.execute("DELETE FROM outputs WHERE chat_id = ? AND path = ?", (chat_id, str(path)))
            self._db.execute("INSERT INTO outputs (chat_id, path, kind, is_dir, created) VALUES (?, ?, ?, ?, ?)",
                             (chat_id, str(path), kind, int(is_dir), time.time()))
            self._db.commit()

    def outputs(self, chat_id: int, limit: int = 30) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT * FROM outputs WHERE chat_id = ? ORDER BY created DESC, id DESC "
                                            "LIMIT ?", (chat_id, limit))]

    def output(self, output_id: int) -> dict | None:
        rows = self._rows("SELECT * FROM outputs WHERE id = ?", (output_id,))
        return dict(rows[0]) if rows else None

    def delete_chat(self, chat_id: int) -> list[str]:
        """Remove a chat with everything that came from it. Returns the topics it touched."""
        topics = sorted({s["topic"] for s in self.segments(chat_id=chat_id)})
        for doc_id in self.document_keys(f"chat:{chat_id}:").values():
            self.delete_document(doc_id)
        with self._lock:
            db = self._db
            task_ids = [r["id"] for r in db.execute("SELECT id FROM tasks WHERE chat_id = ?", (chat_id,))]
            for tid in task_ids:
                notes = [r["id"] for r in db.execute("SELECT id FROM agent_notes WHERE task_id = ?", (tid,))]
                db.executemany("DELETE FROM search_index WHERE kind = 'agent_note' AND ref_id = ?",
                               [(n,) for n in notes])
                db.execute("DELETE FROM agent_notes WHERE task_id = ?", (tid,))
                db.execute("DELETE FROM steps WHERE task_id = ?", (tid,))
            db.execute("DELETE FROM tasks WHERE chat_id = ?", (chat_id,))
            for kind, table in (("message", "messages"), ("chat_note", "chat_notes")):
                ids = [r["id"] for r in db.execute(f"SELECT id FROM {table} WHERE chat_id = ?", (chat_id,))]
                db.executemany(f"DELETE FROM search_index WHERE kind = '{kind}' AND ref_id = ?", [(i,) for i in ids])
                db.execute(f"DELETE FROM {table} WHERE chat_id = ?", (chat_id,))
            db.execute("DELETE FROM chat_segments WHERE chat_id = ?", (chat_id,))
            db.execute("DELETE FROM outputs WHERE chat_id = ?", (chat_id,))
            db.execute("UPDATE reminders SET state = 'cancelled' WHERE chat_id = ?", (chat_id,))
            db.execute("DELETE FROM chats WHERE id = ?", (chat_id,))
            db.commit()
        return topics

    def search_chats(self, query: str, limit: int = 20, per_chat: int = 3) -> list[dict]:
        """Chats whose name or messages match, newest first, each with up to `per_chat` matching snippets.
        Matched words sit between MARK_START and MARK_END (private-use characters, so user text can't fake them)."""
        terms = self._terms(query)
        found: dict[int, dict] = {}
        if terms:
            match = " OR ".join(f'"{t}"*' for t in terms)
            try:
                rows = self._rows(
                    "SELECT m.id, m.chat_id, snippet(search_index, 2, char(57344), char(57345), '…', 10) AS snip "
                    "FROM search_index s JOIN messages m ON m.id = s.ref_id "
                    "WHERE search_index MATCH ? AND s.kind = 'message' ORDER BY bm25(search_index) LIMIT 300",
                    (match,))
            except sqlite3.OperationalError:
                rows = []
            for r in rows:
                hit = found.setdefault(int(r["chat_id"]), {"chat_id": int(r["chat_id"]), "snippets": []})
                if len(hit["snippets"]) < per_chat:
                    hit["snippets"].append({"message_id": int(r["id"]), "text": r["snip"]})
        like = "%" + query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        if query.strip():
            for r in self._rows("SELECT id FROM chats WHERE title LIKE ? ESCAPE '\\'", (like,)):
                found.setdefault(int(r["id"]), {"chat_id": int(r["id"]), "snippets": []})
        titles = dict(self.chats())
        out = [dict(h, title=titles[h["chat_id"]]) for h in found.values() if h["chat_id"] in titles]
        out.sort(key=lambda h: h["chat_id"], reverse=True)
        return out[:limit]

    def chats(self) -> list[tuple[int, str]]:
        return [(r["id"], r["title"]) for r in self._rows("SELECT id, title FROM chats ORDER BY id DESC")]

    def add_message(self, chat_id: int, role: str, text: str, model: str = "", private: bool = False,
                    topic: str = "") -> int:
        mid = self._write(
            "INSERT INTO messages (chat_id, role, text, created, model, private, topic) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (chat_id, role, text, time.time(), model, int(private), topic),
        )
        self._index("message", mid, text)
        if role == "assistant":
            for listener in list(self.listeners):
                try:
                    listener(chat_id)
                except Exception:   # the archive must never break a chat
                    pass
        return mid

    def set_message_topic(self, message_id: int, topic: str) -> None:
        self._write("UPDATE messages SET topic = ? WHERE id = ?", (topic, message_id))

    def messages(self, chat_id: int) -> list[Message]:
        rows = self._rows("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [Message(r["id"], r["chat_id"], r["role"], r["text"], r["created"], r["model"], bool(r["private"]),
                        r["topic"]) for r in rows]

    def chat_title(self, chat_id: int) -> str:
        rows = self._rows("SELECT title FROM chats WHERE id = ?", (chat_id,))
        return rows[0]["title"] if rows else "chat"

    def chat_created(self, chat_id: int) -> float:
        rows = self._rows("SELECT created FROM chats WHERE id = ?", (chat_id,))
        return float(rows[0]["created"]) if rows else time.time()

    # -- chat segments: each chat split by topic, linked across chats ----------------
    def set_segments(self, chat_id: int, segments: list[tuple[str, int, int, int, float]]) -> None:
        """Replace this chat's segments: (topic, first_msg, last_msg, doc_id, started)."""
        with self._lock:
            self._db.execute("DELETE FROM chat_segments WHERE chat_id = ?", (chat_id,))
            self._db.executemany("INSERT INTO chat_segments (chat_id, topic, first_msg, last_msg, doc_id, started) "
                                 "VALUES (?, ?, ?, ?, ?, ?)", [(chat_id, *seg) for seg in segments])
            self._db.commit()

    def segments(self, chat_id: int | None = None, topic: str | None = None) -> list[dict]:
        where, args = [], []
        if chat_id is not None:
            where.append("s.chat_id = ?")
            args.append(chat_id)
        if topic is not None:
            where.append("s.topic = ?")
            args.append(topic)
        sql = ("SELECT s.*, c.title AS chat_title, d.private FROM chat_segments s JOIN chats c ON c.id = s.chat_id "
               "JOIN documents d ON d.id = s.doc_id" + (" WHERE " + " AND ".join(where) if where else "")
               + " ORDER BY s.started DESC")
        return [dict(r) for r in self._rows(sql, tuple(args))]

    def topics(self, limit: int = 200) -> list[dict]:
        """Every topic with how many chats talked about it and when it last came up, newest first."""
        rows = self._rows("SELECT topic, COUNT(DISTINCT chat_id) AS chats, COUNT(*) AS parts, MAX(started) AS last "
                          "FROM chat_segments GROUP BY topic ORDER BY last DESC LIMIT ?", (limit,))
        return [dict(r) for r in rows]

    # -- notes and memories ---------------------------------------------
    def add_chat_note(self, chat_id: int, text: str) -> int:
        nid = self._write(
            "INSERT INTO chat_notes (chat_id, text, created) VALUES (?, ?, ?)", (chat_id, text, time.time())
        )
        self._index("chat_note", nid, text)
        return nid

    def chat_notes(self, chat_id: int) -> list[Note]:
        rows = self._rows("SELECT * FROM chat_notes WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [Note(r["id"], r["text"], r["created"]) for r in rows]

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        """Remove one message from a chat (the owner's choice). True when it existed."""
        with self._lock:
            cur = self._db.execute("DELETE FROM messages WHERE id = ? AND chat_id = ?", (message_id, chat_id))
            self._db.commit()
            gone = cur.rowcount > 0
        if gone:
            self._unindex("message", message_id)
        return gone

    def chat_tasks(self, chat_id: int) -> list[dict]:
        """The requests of a chat, oldest first: id, created, status."""
        return [{"id": r["id"], "created": float(r["created"]), "status": r["status"]}
                for r in self._rows("SELECT id, created, status FROM tasks WHERE chat_id = ? ORDER BY id", (chat_id,))]

    def delete_chat_note(self, note_id: int) -> None:
        self._write("DELETE FROM chat_notes WHERE id = ?", (note_id,))
        self._unindex("chat_note", note_id)

    def add_memory(self, text: str, source: str = "") -> int:
        mid = self._write("INSERT INTO memories (text, created, source) VALUES (?, ?, ?)",
                          (text, time.time(), source))
        self._index("memory", mid, text)
        return mid

    def memories(self) -> list[Note]:
        return [Note(r["id"], r["text"], r["created"], r["source"])
                for r in self._rows("SELECT * FROM memories ORDER BY id")]

    def delete_memory(self, memory_id: int) -> None:
        self._write("DELETE FROM memories WHERE id = ?", (memory_id,))
        self._unindex("memory", memory_id)

    def wipe_memories(self) -> None:
        self._write("DELETE FROM memories")
        self._unindex("memory")

    # -- tasks and steps ---------------------------------------------------
    def create_task(self, chat_id: int, text: str) -> int:
        return self._write(
            "INSERT INTO tasks (chat_id, text, status, created) VALUES (?, ?, 'new', ?)",
            (chat_id, text, time.time()),
        )

    def set_task_status(self, task_id: int, status: str, answer: str = "") -> None:
        finished = 0.0 if status in ("new", "running") else time.time()
        self._write("UPDATE tasks SET status = ?, answer = ?, finished = ? WHERE id = ?", (status, answer, finished, task_id))

    def list_tasks(self, statuses: tuple[str, ...] = (), q: str = "", since: float = 0.0, limit: int = 300) -> list[dict]:
        """Tasks newest first, with their chat's title and how many steps they took (the Tasks page)."""
        where, args = ["t.created >= ?"], [since]
        if statuses:
            where.append(f"t.status IN ({','.join('?' * len(statuses))})")
            args += list(statuses)
        if q:
            where.append("(t.text LIKE ? ESCAPE '\\' OR t.answer LIKE ? ESCAPE '\\')")
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [like, like]
        rows = self._rows(
            "SELECT t.id, t.chat_id, t.text, t.status, t.created, t.finished, COALESCE(c.title, '') AS chat, "
            "(SELECT COUNT(*) FROM steps s WHERE s.task_id = t.id) AS steps "
            f"FROM tasks t LEFT JOIN chats c ON c.id = t.chat_id WHERE {' AND '.join(where)} "
            "ORDER BY t.id DESC LIMIT ?", (*args, limit))
        return [dict(r) for r in rows]

    def task_status_counts(self) -> dict[str, int]:
        return {r["status"]: r["n"] for r in self._rows("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status")}

    def delete_task(self, task_id: int) -> bool:
        """Removes a task's record, steps and notes; the chat and its messages stay."""
        with self._lock:
            cur = self._db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._db.execute("DELETE FROM steps WHERE task_id = ?", (task_id,))
            self._db.execute("DELETE FROM agent_notes WHERE task_id = ?", (task_id,))
            self._db.commit()
            return cur.rowcount > 0

    def task(self, task_id: int) -> dict:
        rows = self._rows("SELECT * FROM tasks WHERE id = ?", (task_id,))
        return dict(rows[0]) if rows else {}

    def latest_task(self, chat_id: int) -> dict:
        rows = self._rows("SELECT * FROM tasks WHERE chat_id = ? ORDER BY id DESC LIMIT 1", (chat_id,))
        return dict(rows[0]) if rows else {}

    def add_step(self, task_id: int, agent: str, goal: str, inputs: dict) -> int:
        return self._write(
            "INSERT INTO steps (task_id, agent, goal, inputs, status, created) VALUES (?, ?, ?, ?, 'running', ?)",
            (task_id, agent, goal, json.dumps(inputs), time.time()),
        )

    def finish_step(self, step_id: int, status: str, result: str, data: dict | None = None) -> None:
        self._write(
            "UPDATE steps SET status = ?, result = ?, data = ? WHERE id = ?",
            (status, result, json.dumps(data or {}), step_id),
        )

    def steps(self, task_id: int) -> list[Step]:
        rows = self._rows("SELECT * FROM steps WHERE task_id = ? ORDER BY id", (task_id,))
        return [
            Step(r["id"], r["task_id"], r["agent"], r["goal"], json.loads(r["inputs"]), r["status"], r["result"],
                 json.loads(r["data"]), float(r["created"] or 0.0))
            for r in rows
        ]

    def agent_activity(self) -> dict[str, dict]:
        """Per agent: its last step (when, status, goal) and how many steps it ran, from the real steps table."""
        out: dict[str, dict] = {}
        for r in self._rows("SELECT agent, COUNT(*) AS runs, MAX(id) AS last_id FROM steps GROUP BY agent"):
            out[r["agent"]] = {"runs": int(r["runs"]), "last_id": int(r["last_id"])}
        ids = [v["last_id"] for v in out.values()]
        if ids:
            marks = ",".join("?" for _ in ids)
            for r in self._rows(f"SELECT s.id, s.agent, s.status, s.goal, s.result, s.created, s.task_id, t.chat_id "
                                f"FROM steps s LEFT JOIN tasks t ON t.id = s.task_id WHERE s.id IN ({marks})", tuple(ids)):
                out[r["agent"]].update({"status": r["status"], "goal": r["goal"], "result": r["result"],
                                        "when": float(r["created"] or 0), "task_id": int(r["task_id"]),
                                        "chat_id": int(r["chat_id"] or 0)})
        return out

    def agent_steps(self, agent: str, limit: int = 10) -> list[dict]:
        rows = self._rows("SELECT s.id, s.status, s.goal, s.result, s.created, s.task_id, t.chat_id FROM steps s "
                          "LEFT JOIN tasks t ON t.id = s.task_id WHERE s.agent = ? ORDER BY s.id DESC LIMIT ?",
                          (agent, limit))
        return [dict(r) for r in rows]

    def mark_interrupted(self) -> int:
        with self._lock:
            cur = self._db.execute("UPDATE tasks SET status = 'interrupted' WHERE status = 'running'")
            self._db.execute("UPDATE steps SET status = 'interrupted' WHERE status = 'running'")
            self._db.commit()
            return cur.rowcount

    # -- shared agent notes -----------------------------------------------
    def add_agent_note(self, task_id: int, agent: str, text: str) -> None:
        nid = self._write(
            "INSERT INTO agent_notes (task_id, agent, text, created) VALUES (?, ?, ?, ?)",
            (task_id, agent, text, time.time()),
        )
        self._index("agent_note", nid, text)

    def agent_notes(self, task_id: int) -> list[tuple[str, str]]:
        rows = self._rows("SELECT agent, text FROM agent_notes WHERE task_id = ? ORDER BY id", (task_id,))
        return [(r["agent"], r["text"]) for r in rows]

    # -- audit ------------------------------------------------------------
    def audit(self, actor: str, action: str, detail: dict) -> None:
        self._write(
            "INSERT INTO audit_log (actor, action, detail, created) VALUES (?, ?, ?, ?)",
            (actor, action, json.dumps(detail, default=str), time.time()),
        )

    def audit_log(self, limit: int = 100) -> list[dict]:
        rows = self._rows("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
        return [{**dict(r), "detail": json.loads(r["detail"])} for r in rows]

    # -- approvals ----------------------------------------------------------
    def add_approval(self, kind: str, summary: str, payload: dict, chat_id: int = 0, inline: bool = False) -> int:
        if chat_id or inline:
            return self._write("INSERT INTO approvals (kind, summary, payload, status, created, chat_id, inline) "
                               "VALUES (?, ?, ?, 'pending', ?, ?, ?)",
                               (kind, summary, json.dumps(payload), time.time(), chat_id, int(inline)))
        return self._add_approval(kind, summary, payload)

    def asks_for_chat(self, chat_id: int) -> list[Approval]:
        """Small approvals waiting inside this chat (shown under the messages, not on the Approvals page)."""
        return [self._approval(r) for r in self._rows(
            "SELECT * FROM approvals WHERE status = 'pending' AND inline = 1 AND chat_id = ? ORDER BY id", (chat_id,))]

    def big_approvals(self) -> list[Approval]:
        return [self._approval(r) for r in self._rows(
            "SELECT * FROM approvals WHERE status = 'pending' AND inline = 0 ORDER BY id")]

    def approval_chat(self, approval_id: int) -> tuple[int, bool]:
        rows = self._rows("SELECT chat_id, inline FROM approvals WHERE id = ?", (approval_id,))
        return (int(rows[0]["chat_id"]), bool(rows[0]["inline"])) if rows else (0, False)

    # -- remembered approvals: allowed once, allowed in every chat ------------------------------
    def add_allow_rule(self, kind: str, key: str, label: str) -> None:
        if key:
            self._write("INSERT OR IGNORE INTO allow_rules (kind, key, label, created) VALUES (?, ?, ?, ?)",
                        (kind, key, label[:200], time.time()))

    def allowed(self, kind: str, keys: list[str]) -> bool:
        keys = [k for k in keys if k]
        if not keys:
            return False
        marks = ",".join("?" for _ in keys)
        return bool(self._rows(f"SELECT 1 FROM allow_rules WHERE kind = ? AND key IN ({marks})", (kind, *keys)))

    def allow_rules(self) -> list[dict]:
        return [dict(r) for r in self._rows("SELECT * FROM allow_rules ORDER BY created DESC")]

    def remove_allow_rule(self, rule_id: int) -> None:
        self._write("DELETE FROM allow_rules WHERE id = ?", (rule_id,))

    def _add_approval(self, kind: str, summary: str, payload: dict) -> int:
        return self._write(
            "INSERT INTO approvals (kind, summary, payload, status, created) VALUES (?, ?, ?, 'pending', ?)",
            (kind, summary, json.dumps(payload), time.time()),
        )

    def _approval(self, r: sqlite3.Row) -> Approval:
        return Approval(r["id"], r["kind"], r["summary"], json.loads(r["payload"]), r["status"])

    def approvals(self, status: str = "pending") -> list[Approval]:
        return [self._approval(r) for r in self._rows("SELECT * FROM approvals WHERE status = ? ORDER BY id", (status,))]

    def approval(self, approval_id: int) -> Approval:
        rows = self._rows("SELECT * FROM approvals WHERE id = ?", (approval_id,))
        if not rows:
            raise KeyError(approval_id)
        return self._approval(rows[0])

    def claim_approval(self, approval_id: int) -> bool:
        """Atomically move a pending approval to 'running'. Only one caller can win."""
        with self._lock:
            cur = self._db.execute("UPDATE approvals SET status = 'running' WHERE id = ? AND status = 'pending'",
                                   (approval_id,))
            self._db.commit()
            return cur.rowcount == 1

    def set_approval_status(self, approval_id: int, status: str) -> None:
        self._write("UPDATE approvals SET status = ? WHERE id = ?", (status, approval_id))

    # -- costs ------------------------------------------------------------
    def add_cost(self, provider: str, model: str, usd: float, tokens_in: int, tokens_out: int,
                 chat_id: int = 0) -> None:
        self._write(
            "INSERT INTO costs (provider, model, usd, tokens_in, tokens_out, created, chat_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (provider, model, usd, tokens_in, tokens_out, time.time(), int(chat_id or 0)),
        )

    def total_cost(self, since: float = 0.0) -> float:
        return float(self._rows("SELECT COALESCE(SUM(usd), 0) AS s FROM costs WHERE created >= ?", (since,))[0]["s"])

    def cost_report(self, since: float) -> dict:
        """What paid AI cost since a moment: by model, by day and by chat."""
        by_model = [dict(r) for r in self._rows(
            "SELECT provider, model, SUM(usd) AS usd, COUNT(*) AS calls, SUM(tokens_in) AS tin, "
            "SUM(tokens_out) AS tout FROM costs WHERE created >= ? GROUP BY provider, model ORDER BY usd DESC",
            (since,))]
        by_day = [dict(r) for r in self._rows(
            "SELECT date(created, 'unixepoch', 'localtime') AS day, SUM(usd) AS usd, COUNT(*) AS calls FROM costs "
            "WHERE created >= ? GROUP BY day ORDER BY day", (since,))]
        by_chat = [dict(r) for r in self._rows(
            "SELECT c.chat_id, COALESCE(h.title, 'Background jobs') AS title, SUM(c.usd) AS usd, COUNT(*) AS calls "
            "FROM costs c LEFT JOIN chats h ON h.id = c.chat_id WHERE c.created >= ? GROUP BY c.chat_id "
            "ORDER BY usd DESC LIMIT 15", (since,))]
        return {"by_model": by_model, "by_day": by_day, "by_chat": by_chat}

    # -- recall: find the right memory -----------------------------------------
    @staticmethod
    def _terms(query: str) -> list[str]:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1 and w not in STOPWORDS]
        return list(dict.fromkeys(words))[:12]

    def _lookup(self, kind: str, ref_id: int) -> tuple[str, str, float] | None:
        if kind == "memory":
            rows = self._rows("SELECT text, source, created FROM memories WHERE id = ?", (ref_id,))
        elif kind == "chat_note":
            rows = self._rows("SELECT n.text, 'chat: ' || c.title AS source, n.created FROM chat_notes n "
                              "LEFT JOIN chats c ON c.id = n.chat_id WHERE n.id = ?", (ref_id,))
        elif kind == "agent_note":
            rows = self._rows("SELECT text, 'agent: ' || agent AS source, created FROM agent_notes WHERE id = ?",
                              (ref_id,))
        elif kind == "chunk":
            rows = self._rows("SELECT c.text, d.title AS source, d.updated AS created FROM chunks c "
                              "JOIN documents d ON d.id = c.doc_id WHERE c.id = ? AND d.private = 0", (ref_id,))
        elif kind == "message":
            rows = self._rows("SELECT m.text, 'chat: ' || c.title AS source, m.created FROM messages m "
                              "LEFT JOIN chats c ON c.id = m.chat_id WHERE m.id = ?", (ref_id,))
        else:
            return None
        return (rows[0]["text"], rows[0]["source"] or "", rows[0]["created"]) if rows else None

    def recall(self, query: str, limit: int = 8,
               kinds: tuple[str, ...] = ("memory", "chat_note", "agent_note")) -> list[Recalled]:
        """Best matches for a request: word forms and partial words match, better and newer matches rank first."""
        terms = self._terms(query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        marks = ",".join("?" for _ in kinds)
        try:
            rows = self._rows(
                f"SELECT kind, ref_id, bm25(search_index) AS rank FROM search_index "
                f"WHERE search_index MATCH ? AND kind IN ({marks}) ORDER BY rank LIMIT 60", (match, *kinds))
        except sqlite3.OperationalError:
            return []
        now = time.time()
        out = []
        for r in rows:
            found = self._lookup(r["kind"], int(r["ref_id"]))
            if found is None:
                continue
            text, source, created = found
            age_days = max(0.0, (now - created) / 86400)
            score = -float(r["rank"]) * KIND_WEIGHT.get(r["kind"], 1.0) * (1 + 0.25 * math.exp(-age_days / RECENCY_DAYS))
            out.append(Recalled(r["kind"], int(r["ref_id"]), text, source, created, score))
        out.sort(key=lambda h: h.score, reverse=True)
        return out[:limit]

    # -- search -------------------------------------------------------------
    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        terms = " ".join('"' + t.replace('"', '""') + '"' for t in query.split())
        if not terms:
            return []
        try:
            rows = self._rows(
                "SELECT kind, ref_id, snippet(search_index, 2, '[', ']', '…', 12) AS snip FROM search_index "
                "WHERE search_index MATCH ? LIMIT ?",
                (terms.replace("\x00", ""), limit),
            )
        except sqlite3.OperationalError:
            return []
        return [SearchHit(r["kind"], int(r["ref_id"]), r["snip"]) for r in rows]

    # -- documents: long text split into chunks ------------------------------------
    def add_document(self, key: str, title: str, source: str, text: str, private: bool) -> int:
        """Save a long text (a web page, a file) as searchable chunks. Saving the same text again does nothing."""
        from prometheus.memory.chunking import MAX_DOC_CHARS, chunk_text, text_hash

        text = (text or "")[:MAX_DOC_CHARS]
        digest, now = text_hash(text), time.time()
        old = self._rows("SELECT id, hash, private FROM documents WHERE key = ?", (key,))
        if old and old[0]["hash"] == digest and bool(old[0]["private"]) == bool(private):
            return int(old[0]["id"])
        parents = chunk_text(text)
        children: list[tuple[int, str]] = []
        with self._lock:
            if old:
                doc_id = int(old[0]["id"])
                self._drop_chunks(doc_id)
                self._db.execute("UPDATE documents SET title = ?, source = ?, private = ?, hash = ?, chars = ?, "
                                 "updated = ? WHERE id = ?", (title, source, int(private), digest, len(text), now,
                                                              doc_id))
            else:
                doc_id = self._db.execute(
                    "INSERT INTO documents (key, title, source, private, hash, chars, created, updated) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (key, title, source, int(private), digest, len(text), now, now)).lastrowid
            seq = 0
            for parent in parents:
                pid = self._db.execute("INSERT INTO chunks (doc_id, parent_id, seq, text, start, end) "
                                       "VALUES (?, NULL, ?, ?, ?, ?)",
                                       (doc_id, seq, parent.text, parent.start, parent.end)).lastrowid
                seq += 1
                for child in parent.children:
                    cid = self._db.execute("INSERT INTO chunks (doc_id, parent_id, seq, text, start, end) "
                                           "VALUES (?, ?, ?, ?, ?, ?)",
                                           (doc_id, pid, seq, child.text, child.start, child.end)).lastrowid
                    seq += 1
                    self._db.execute("INSERT INTO search_index (kind, ref_id, body) VALUES ('chunk', ?, ?)",
                                     (cid, child.text))
                    children.append((cid, child.text))
            self._db.commit()
        self._embed_chunks(children)
        return int(doc_id)

    def _drop_chunks(self, doc_id: int) -> None:
        ids = [r["id"] for r in self._db.execute("SELECT id FROM chunks WHERE doc_id = ?", (doc_id,))]
        for i in range(0, len(ids), 500):
            part = ids[i:i + 500]
            marks = ",".join("?" for _ in part)
            self._db.execute(f"DELETE FROM search_index WHERE kind = 'chunk' AND ref_id IN ({marks})", part)
            self._db.execute(f"DELETE FROM chunk_vectors WHERE chunk_id IN ({marks})", part)
        self._db.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))

    def _embed_chunks(self, children: list[tuple[int, str]]) -> None:
        embedder = self.embedder
        if embedder is None or not children:
            return
        for i in range(0, len(children), EMBED_BATCH):
            batch = children[i:i + EMBED_BATCH]
            try:
                vectors = embedder.embed([t for _, t in batch])
            except Exception:   # no embedding model: plain word search still works
                return
            with self._lock:
                for (cid, _), vec in zip(batch, vectors):
                    self._db.execute("INSERT OR REPLACE INTO chunk_vectors (chunk_id, model, vec) VALUES (?, ?, ?)",
                                     (cid, embedder.model, array("f", vec).tobytes()))
                self._db.commit()

    def delete_document(self, doc_id: int) -> None:
        with self._lock:
            self._drop_chunks(doc_id)
            self._db.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
            self._db.commit()

    def documents(self, limit: int = 200, chats: bool = False) -> list[Document]:
        """Saved pages and files (or, with chats=True, the saved chat segments)."""
        rows = self._rows("SELECT d.*, (SELECT COUNT(*) FROM chunks c WHERE c.doc_id = d.id "
                          "AND c.parent_id IS NOT NULL) AS n FROM documents d WHERE (d.key LIKE 'chat:%') = ? "
                          "ORDER BY d.updated DESC LIMIT ?", (int(chats), limit))
        return [Document(r["id"], r["key"], r["title"], r["source"], bool(r["private"]), r["chars"], r["n"],
                         r["updated"]) for r in rows]

    def document_keys(self, prefix: str) -> dict[str, int]:
        esc = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        rows = self._rows("SELECT id, key FROM documents WHERE key LIKE ? ESCAPE '\\'", (esc + "%",))
        return {r["key"]: int(r["id"]) for r in rows}

    def document_id(self, key: str) -> int | None:
        rows = self._rows("SELECT id FROM documents WHERE key = ?", (key,))
        return int(rows[0]["id"]) if rows else None

    def _word_ranks(self, query: str, where: str, args: tuple) -> list[int]:
        terms = self._terms(query)
        if not terms:
            return []
        match = " OR ".join(f'"{t}"*' for t in terms)
        try:
            rows = self._rows(
                "SELECT s.ref_id AS id FROM search_index s JOIN chunks c ON c.id = s.ref_id "
                f"JOIN documents d ON d.id = c.doc_id WHERE search_index MATCH ? AND s.kind = 'chunk' {where} "
                "ORDER BY bm25(search_index) LIMIT 80", (match, *args))
        except sqlite3.OperationalError:
            return []
        return [int(r["id"]) for r in rows]

    def _meaning_ranks(self, query: str, where: str, args: tuple) -> list[int]:
        embedder = self.embedder
        if embedder is None:
            return []
        try:
            q = embedder.embed([query])[0]
        except Exception:
            return []
        qn = math.sqrt(sum(x * x for x in q)) or 1.0
        rows = self._rows("SELECT v.chunk_id AS id, v.vec FROM chunk_vectors v JOIN chunks c ON c.id = v.chunk_id "
                          f"JOIN documents d ON d.id = c.doc_id WHERE v.model = ? {where}", (embedder.model, *args))
        scored = []
        for r in rows:
            vec = array("f")
            vec.frombytes(r["vec"])
            if len(vec) != len(q):
                continue
            dot = sum(a * b for a, b in zip(q, vec))
            norm = math.sqrt(sum(b * b for b in vec)) or 1.0
            scored.append((dot / (qn * norm), int(r["id"])))
        scored.sort(reverse=True)
        return [cid for sim, cid in scored[:80] if sim > 0.3]

    def search_chunks(self, query: str, limit: int = 4, include_private: bool = False,
                      doc_ids: list[int] | None = None, chats: bool | None = None,
                      exclude_doc_ids: list[int] | tuple = ()) -> list[ChunkHit]:
        """Best passages for a question. Word search and (when available) meaning search are merged by rank;
        each hit carries the small chunk that matched and the bigger parent chunk around it.
        chats=True searches only saved chats, False only pages and files, None both."""
        where, args = "", ()
        if not include_private:
            where += " AND d.private = 0"
        if chats is not None:
            where += " AND d.key LIKE 'chat:%'" if chats else " AND d.key NOT LIKE 'chat:%'"
        if exclude_doc_ids:
            where += f" AND d.id NOT IN ({','.join(str(int(i)) for i in exclude_doc_ids)})"
        if doc_ids is not None:
            if not doc_ids:
                return []
            where += f" AND d.id IN ({','.join('?' for _ in doc_ids)})"
            args = tuple(int(i) for i in doc_ids)
        fused: dict[int, float] = {}
        for ranks in (self._word_ranks(query, where, args), self._meaning_ranks(query, where, args)):
            for rank, cid in enumerate(ranks):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
        if not fused:
            return []
        hits, seen_parents = [], set()
        for cid, score in sorted(fused.items(), key=lambda kv: kv[1], reverse=True):
            rows = self._rows("SELECT c.text, c.parent_id, p.text AS ptext, d.id AS doc_id, d.title, d.source, "
                              "d.private FROM chunks c JOIN chunks p ON p.id = c.parent_id "
                              "JOIN documents d ON d.id = c.doc_id WHERE c.id = ?", (cid,))
            if not rows or rows[0]["parent_id"] in seen_parents:
                continue
            r = rows[0]
            seen_parents.add(r["parent_id"])
            hits.append(ChunkHit(int(r["doc_id"]), r["title"], r["source"], bool(r["private"]), r["text"],
                                 r["ptext"], score))
            if len(hits) >= limit:
                break
        return hits

    def best_passages(self, doc_id: int, question: str, budget: int) -> str:
        """The parts of one document that best answer a question, in reading order, within `budget` characters."""
        hits = self.search_chunks(question, limit=12, include_private=True, doc_ids=[doc_id]) if question else []
        rows = self._rows("SELECT text, start FROM chunks WHERE doc_id = ? AND parent_id IS NULL ORDER BY seq",
                          (doc_id,))
        if not hits:
            out, used = [], 0
            for r in rows:
                if used + len(r["text"]) > budget:
                    break
                out.append(r["text"])
                used += len(r["text"])
            return "\n\n".join(out) or (rows[0]["text"][:budget] if rows else "")
        start_of = {r["text"]: r["start"] for r in rows}
        picked, used = [], 0
        for h in hits:
            if used + len(h.context) > budget:
                continue
            picked.append(h.context)
            used += len(h.context)
        if not picked:
            return hits[0].context[:budget]
        picked.sort(key=lambda t: start_of.get(t, 0))
        return "\n\n[…]\n\n".join(picked)
