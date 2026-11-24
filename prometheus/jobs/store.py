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
            self._db.commit()
            job_id = cur.lastrowid
            return self.get_job(job_id)

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

            return self.update_job(
                job_id,
                state=new_state,
                started=started,
                finished=finished,
                error=error,
                progress=progress,
            )

    def recover_after_restart(self) -> int:
        """On startup: mark any jobs left in 'running' as 'paused' with restart reason."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE jobs SET state = 'paused', error = 'Prometheus restarted' WHERE state = 'running'"
            )
            self._db.commit()
            return cur.rowcount

    def pause_all(self, reason: str = "Stopped all jobs") -> int:
        """Pause all running jobs when killswitch is triggered."""
        with self._lock:
            cur = self._db.execute(
                "UPDATE jobs SET state = 'paused', error = ? WHERE state = 'running'",
                (reason,),
            )
            self._db.commit()
            return cur.rowcount
