"""SQLite storage for background and multi-agent jobs (Prometheus M5)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from prometheus.jobs.states import (
    ALL_STATES,
    CANCELLED,
    DONE,
    FAILED,
    PAUSED,
    PENDING,
    RUNNING,
    TERMINAL_STATES,
    validate_transition,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    state TEXT NOT NULL,
    parent_id INTEGER,
    created REAL NOT NULL,
    started REAL NOT NULL DEFAULT 0.0,
    finished REAL NOT NULL DEFAULT 0.0,
    progress TEXT NOT NULL DEFAULT '',
    error TEXT NOT NULL DEFAULT '',
    retries INTEGER NOT NULL DEFAULT 0,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs (state);
CREATE INDEX IF NOT EXISTS idx_jobs_parent ON jobs (parent_id);
CREATE TABLE IF NOT EXISTS job_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    at REAL NOT NULL,
    state TEXT NOT NULL,
    text TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_job_events_seq ON job_events (job_id, seq);
"""


@dataclass
class Job:
    id: int
    kind: str
    title: str
    state: str
    created: float
    parent_id: int | None = None
    started: float = 0.0
    finished: float = 0.0
    progress: str = ""
    error: str = ""
    retries: int = 0
    payload: dict = field(default_factory=dict)


def _row_to_job(row: sqlite3.Row | tuple) -> Job:
    payload_val = row[11]
    if isinstance(payload_val, str):
        try:
            payload = json.loads(payload_val) if payload_val else {}
        except Exception:
            payload = {"raw": payload_val}
    elif isinstance(payload_val, dict):
        payload = payload_val
    else:
        payload = {}

    return Job(
        id=int(row[0]),
        kind=str(row[1]),
        title=str(row[2]),
        state=str(row[3]),
        parent_id=int(row[4]) if row[4] is not None else None,
        created=float(row[5]),
        started=float(row[6] or 0.0),
        finished=float(row[7] or 0.0),
        progress=str(row[8] or ""),
        error=str(row[9] or ""),
        retries=int(row[10] or 0),
        payload=payload,
    )


class JobStore:
    """Manages persistence of background and DAG jobs in SQLite.
    
    Uses WAL mode transactions under a reentrant lock to ensure
    crash-resilient atomic writes.
    """

    def __init__(self, db_or_path: Path | str | sqlite3.Connection | None = None,
                 lock: threading.RLock | None = None):
        self._lock = lock or threading.RLock()
        if db_or_path is None:
            from prometheus.config import data_dir
            db_path = data_dir() / "memory.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(db_path), check_same_thread=False)
            self._owns_db = True
        elif isinstance(db_or_path, (str, Path)):
            db_path = Path(db_or_path)
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(db_path), check_same_thread=False)
            self._owns_db = True
        elif isinstance(db_or_path, sqlite3.Connection):
            self._db = db_or_path
            self._owns_db = False
        elif hasattr(db_or_path, "_db"):  # e.g. MemoryStore Store instance
            self._db = db_or_path._db
            self._lock = getattr(db_or_path, "_lock", self._lock)
            self._owns_db = False
        else:
            raise TypeError(f"Unsupported db_or_path: {type(db_or_path)}")

        self._db.row_factory = sqlite3.Row
        with self._lock:
            try:
                self._db.execute("PRAGMA journal_mode=WAL")
            except sqlite3.OperationalError:
                pass  # e.g. in-memory or already locked connection
            self._db.executescript(SCHEMA)
            self._db.commit()

    def create_job(self, kind: str, title: str, parent_id: int | None = None,
                   payload: dict | None = None, state: str = "pending",
                   created: float | None = None) -> Job:
        created_ts = time.time() if created is None else float(created)
        payload_json = json.dumps(payload or {}, ensure_ascii=False)
        with self._lock:
            cur = self._db.execute(
                """INSERT INTO jobs (kind, title, state, parent_id, created, started, finished, progress, error, retries, payload)
                   VALUES (?, ?, ?, ?, ?, 0.0, 0.0, '', '', 0, ?)""",
                (kind, title, state, parent_id, created_ts, payload_json),
            )
            job_id = cur.lastrowid
            self._event(job_id, state, "", created_ts)
            self._db.commit()
            return self.get_job(job_id)

    # -- the job's numbered events (M6 6.1): every state change, in order, for the runtime's events() -------------
    _EVENT_SQL = ("INSERT INTO job_events (job_id, seq, at, state, text) VALUES "
                  "(?, COALESCE((SELECT MAX(seq) FROM job_events WHERE job_id = ?), 0) + 1, ?, ?, ?)")

    def _event(self, job_id: int, state: str, text: str = "", at: float | None = None) -> None:
        """Record one event (the caller holds the lock and commits)."""
        self._db.execute(self._EVENT_SQL, (job_id, job_id, time.time() if at is None else at, state, str(text)[:500]))

    def _events_for(self, where: str, params: tuple, state: str, text: str) -> None:
        """One event for every job a bulk UPDATE is about to change (same lock and transaction). `where` is always
        one of this file's fixed strings, never outside text."""
        ids = [r[0] for r in self._db.execute(f"SELECT id FROM jobs WHERE {where} ORDER BY id", params).fetchall()]
        now = time.time()
        for jid in ids:
            self._event(jid, state, text, now)

    def events(self, job_id: int, after: int = 0) -> list[dict]:
        """The job's events after number `after`, oldest first: {seq, at, state, text}."""
        with self._lock:
            rows = self._db.execute("SELECT seq, at, state, text FROM job_events WHERE job_id = ? AND seq > ? "
                                    "ORDER BY seq", (job_id, int(after))).fetchall()
        return [{"seq": int(r[0]), "at": float(r[1]), "state": str(r[2]), "text": str(r[3])} for r in rows]

    def get_job(self, job_id: int) -> Job | None:
        with self._lock:
            cur = self._db.execute(
                """SELECT id, kind, title, state, parent_id, created, started, finished, progress, error, retries, payload
                   FROM jobs WHERE id = ?""",
                (job_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            return _row_to_job(row)

    def list_jobs(self, state: str | None = None, kind: str | None = None,
                  parent_id: int | None = None) -> list[Job]:
        query = "SELECT id, kind, title, state, parent_id, created, started, finished, progress, error, retries, payload FROM jobs WHERE 1=1"
        params: list = []
        if state is not None:
            query += " AND state = ?"
            params.append(state)
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if parent_id is not None:
            query += " AND parent_id = ?"
            params.append(parent_id)
        query += " ORDER BY id ASC"

        with self._lock:
            cur = self._db.execute(query, params)
            return [_row_to_job(r) for r in cur.fetchall()]

    def _select(self, where: str, params: tuple = (), tail: str = "") -> list[Job]:
        with self._lock:
            cur = self._db.execute(
                "SELECT id, kind, title, state, parent_id, created, started, finished, progress, error, retries, payload "
                f"FROM jobs WHERE {where} {tail}", params)
            return [_row_to_job(r) for r in cur.fetchall()]

    def list_for_view(self, finished: int = 8) -> tuple[list[Job], list[Job]]:
        """For the jobs view (M5 audit F6): unfinished top-level jobs, and the latest `finished` that are worth
        showing (graphs, or ones that didn't end done). Always-on loops are left out. Never reads the whole table."""
        not_service = "COALESCE(json_extract(payload, '$.service'), 0) != 1"
        active = self._select(f"parent_id IS NULL AND state IN ('pending', 'running', 'paused') AND {not_service}",
                              tail="ORDER BY id ASC")
        ended = self._select(f"parent_id IS NULL AND state IN ('done', 'failed', 'cancelled') AND {not_service} "
                             "AND (json_extract(payload, '$.graph') = 1 OR state != 'done')", (),
                             f"ORDER BY COALESCE(NULLIF(finished, 0), created) DESC LIMIT {int(finished)}")
        return active, ended

    def list_unfinished(self) -> list[Job]:
        return self._select("state IN ('pending', 'running', 'paused')", tail="ORDER BY id ASC")

    def list_with_programs(self) -> list[Job]:
        """Jobs whose record lists programs (written by limits.track), for the start-up clean-up."""
        return self._select("json_array_length(json_extract(payload, '$.programs')) > 0", tail="ORDER BY id ASC")

    def prune_finished(self, days: int = 30) -> int:
        """Delete finished jobs that ended more than `days` ago (a graph with its steps), so the table doesn't grow
        for ever on the laptop (M5 audit F6). Unfinished jobs are never deleted. Returns how many rows went."""
        cutoff = time.time() - days * 86400
        with self._lock:
            old = "state IN ('done', 'failed', 'cancelled') AND COALESCE(NULLIF(finished, 0), created) < ?"
            parents = [r[0] for r in self._db.execute(f"SELECT id FROM jobs WHERE parent_id IS NULL AND {old}",
                                                      (cutoff,)).fetchall()]
            gone = 0
            for i in range(0, len(parents), 500):
                part = parents[i:i + 500]
                marks = ",".join("?" * len(part))
                self._db.execute(f"DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE parent_id "
                                 f"IN ({marks}) AND {old}) OR job_id IN ({marks})", (*part, cutoff, *part))
                gone += self._db.execute(f"DELETE FROM jobs WHERE parent_id IN ({marks}) AND {old}",
                                         (*part, cutoff)).rowcount
                gone += self._db.execute(f"DELETE FROM jobs WHERE id IN ({marks})", part).rowcount
            self._db.commit()
            return gone

    def update_job(self, job_id: int, state: str | None = None,
                   started: float | None = None, finished: float | None = None,
                   progress: str | None = None, error: str | None = None,
                   retries: int | None = None, payload: dict | None = None) -> Job | None:
        updates: list[str] = []
        params: list = []
        if state is not None:
            updates.append("state = ?")
            params.append(state)
        if started is not None:
            updates.append("started = ?")
            params.append(float(started))
        if finished is not None:
            updates.append("finished = ?")
            params.append(float(finished))
        if progress is not None:
            updates.append("progress = ?")
            params.append(str(progress))
        if error is not None:
            updates.append("error = ?")
            params.append(str(error))
        if retries is not None:
            updates.append("retries = ?")
            params.append(int(retries))
        if payload is not None:
            updates.append("payload = ?")
            params.append(json.dumps(payload, ensure_ascii=False))

        if not updates:
            return self.get_job(job_id)

        params.append(job_id)
        with self._lock:
            self._db.execute(f"UPDATE jobs SET {', '.join(updates)} WHERE id = ?", params)
            self._db.commit()
            return self.get_job(job_id)

    def merge_payload(self, job_id: int, **fields) -> Job | None:
        """Change some payload fields in one locked read-modify-write (never loses another field)."""
        with self._lock:
            job = self.get_job(job_id)
            if job is None:
                return None
            return self.update_job(job_id, payload={**job.payload, **fields})

    def set_state(self, job_id: int, new_state: str,
                  error: str | None = None, progress: str | None = None) -> Job:
        """Transition a job to a new state enforcing states.py validation."""
        with self._lock:
            job = self.get_job(job_id)
            if job is None:
                raise KeyError(f"Job {job_id} not found")

            validate_transition(job.state, new_state)

            now = time.time()
            started = now if new_state == RUNNING and not job.started else None
            finished = now if new_state in TERMINAL_STATES else None

            out = self.update_job(
                job_id,
                state=new_state,
                started=started,
                finished=finished,
                error=error,
                progress=progress,
            )
            self._event(job_id, new_state, error or "", now)
            self._db.commit()
            return out

    def finish(self, job_id: int, final: str, error: str | None = None) -> Job | None:
        """End a job, also one that waits as paused or pending, through the allowed moves only
        (paused -> pending -> running -> done/failed). Already finished jobs are left as they are."""
        with self._lock:
            job = self.get_job(job_id)
            if job is None or job.state in TERMINAL_STATES:
                return job
            if final != CANCELLED:
                if job.state == PAUSED:
                    self.set_state(job_id, PENDING)
                if self.get_job(job_id).state == PENDING:
                    self.set_state(job_id, RUNNING)
            return self.set_state(job_id, final, error=error)

    def recover_after_restart(self) -> int:
        """On startup: jobs left 'running' become 'paused' (the owner presses Resume). Service loops (payload
        "service") are started again by Prometheus itself, so their old record is closed as cancelled."""
        now = time.time()
        with self._lock:
            self._events_for("state = 'running' AND json_extract(payload, '$.service') = 1", (), CANCELLED,
                             "Ended when Prometheus closed (it starts again by itself)")
            self._db.execute(
                "UPDATE jobs SET state = 'cancelled', finished = ?, "
                "error = 'Ended when Prometheus closed (it starts again by itself)' "
                "WHERE state = 'running' AND json_extract(payload, '$.service') = 1", (now,))
            # a move to Cloudroom still waiting for its receipt has nothing following it any more: it is paused too,
            # so Resume can pick it up (M6 audit A3; the server never runs a request id twice)
            cloud_waiting = "state = 'pending' AND json_extract(payload, '$.where') = 'cloudroom'"
            self._events_for(cloud_waiting, (), PAUSED, "Prometheus restarted")
            self._db.execute(f"UPDATE jobs SET state = 'paused', error = 'Prometheus restarted' WHERE {cloud_waiting}")
            self._events_for("state = 'running'", (), PAUSED, "Prometheus restarted")
            cur = self._db.execute(
                "UPDATE jobs SET state = 'paused', error = 'Prometheus restarted' WHERE state = 'running'"
            )
            self._db.commit()
            return cur.rowcount

    def pause_all(self, reason: str = "Stopped all jobs") -> int:
        """Pause all running jobs when killswitch is triggered. Service loops keep running (they check the kill
        switch themselves), so they are never shown as paused."""
        with self._lock:
            self._events_for("state = 'running' AND COALESCE(json_extract(payload, '$.service'), 0) != 1", (),
                             PAUSED, reason)
            cur = self._db.execute(
                "UPDATE jobs SET state = 'paused', error = ? WHERE state = 'running' "
                "AND COALESCE(json_extract(payload, '$.service'), 0) != 1",
                (reason,),
            )
            self._db.commit()
            return cur.rowcount
