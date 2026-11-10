"""One SQLite file shared by every agent. All writes go through one lock."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
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
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(kind UNINDEXED, ref_id UNINDEXED, body);
"""


@dataclass
class Message:
    id: int
    chat_id: int
    role: str
    text: str
    created: float


@dataclass
class Note:
    id: int
    text: str
    created: float


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
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.executescript(SCHEMA)
            self._db.commit()

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

    def chats(self) -> list[tuple[int, str]]:
        return [(r["id"], r["title"]) for r in self._rows("SELECT id, title FROM chats ORDER BY id DESC")]

    def add_message(self, chat_id: int, role: str, text: str) -> int:
        mid = self._write(
            "INSERT INTO messages (chat_id, role, text, created) VALUES (?, ?, ?, ?)",
            (chat_id, role, text, time.time()),
        )
        self._index("message", mid, text)
        return mid

    def messages(self, chat_id: int) -> list[Message]:
        rows = self._rows("SELECT * FROM messages WHERE chat_id = ? ORDER BY id", (chat_id,))
        return [Message(r["id"], r["chat_id"], r["role"], r["text"], r["created"]) for r in rows]

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

    def delete_chat_note(self, note_id: int) -> None:
        self._write("DELETE FROM chat_notes WHERE id = ?", (note_id,))
        self._unindex("chat_note", note_id)

    def add_memory(self, text: str) -> int:
        mid = self._write("INSERT INTO memories (text, created) VALUES (?, ?)", (text, time.time()))
        self._index("memory", mid, text)
        return mid

    def memories(self) -> list[Note]:
        return [Note(r["id"], r["text"], r["created"]) for r in self._rows("SELECT * FROM memories ORDER BY id")]

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
        self._write("UPDATE tasks SET status = ?, answer = ? WHERE id = ?", (status, answer, task_id))

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
                 json.loads(r["data"]))
            for r in rows
        ]

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
    def add_approval(self, kind: str, summary: str, payload: dict) -> int:
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

    def set_approval_status(self, approval_id: int, status: str) -> None:
        self._write("UPDATE approvals SET status = ? WHERE id = ?", (status, approval_id))

    # -- costs ------------------------------------------------------------
    def add_cost(self, provider: str, model: str, usd: float, tokens_in: int, tokens_out: int) -> None:
        self._write(
            "INSERT INTO costs (provider, model, usd, tokens_in, tokens_out, created) VALUES (?, ?, ?, ?, ?, ?)",
            (provider, model, usd, tokens_in, tokens_out, time.time()),
        )

    def total_cost(self) -> float:
        return float(self._rows("SELECT COALESCE(SUM(usd), 0) AS s FROM costs")[0]["s"])

    # -- search -------------------------------------------------------------
    def search(self, query: str, limit: int = 20) -> list[SearchHit]:
        terms = " ".join('"' + t.replace('"', '""') + '"' for t in query.split())
        if not terms:
            return []
        rows = self._rows(
            "SELECT kind, ref_id, snippet(search_index, 2, '[', ']', '…', 12) AS snip FROM search_index "
            "WHERE search_index MATCH ? LIMIT ?",
            (terms, limit),
        )
        return [SearchHit(r["kind"], int(r["ref_id"]), r["snip"]) for r in rows]
