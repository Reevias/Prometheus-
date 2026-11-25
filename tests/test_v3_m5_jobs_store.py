"""Tests for prometheus.jobs.store (Item 5.1.1)."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
import pytest

from prometheus.jobs.store import JobStore, Job


def test_job_store_creates_table_and_job(tmp_path: Path):
    db_path = tmp_path / "test_jobs.db"
    store = JobStore(db_path)

    job = store.create_job(kind="agent", title="Review Code", payload={"file": "test.py"})
    assert isinstance(job, Job)
    assert job.id > 0
    assert job.kind == "agent"
    assert job.title == "Review Code"
    assert job.state == "pending"
    assert job.parent_id is None
    assert job.created > 0
    assert job.started == 0.0
    assert job.finished == 0.0
    assert job.progress == ""
    assert job.error == ""
    assert job.retries == 0
    assert job.payload == {"file": "test.py"}

    # Verify directly in SQLite schema
    conn = sqlite3.connect(str(db_path))
    cursor = conn.execute("SELECT id, kind, title, state, parent_id, created, started, finished, progress, error, retries, payload FROM jobs WHERE id = ?", (job.id,))
    row = cursor.fetchone()
    assert row is not None
    assert row[0] == job.id
    assert row[1] == "agent"
    assert row[2] == "Review Code"
    assert row[3] == "pending"
    assert row[4] is None
    assert json.loads(row[11]) == {"file": "test.py"}
    conn.close()


def test_job_store_get_and_list(tmp_path: Path):
    db_path = tmp_path / "test_jobs.db"
    store = JobStore(db_path)

    j1 = store.create_job(kind="index", title="File index")
    j2 = store.create_job(kind="sync", title="Sync chats", state="running")
    j3 = store.create_job(kind="index", title="Code index", parent_id=j1.id)

    assert store.get_job(j1.id).title == "File index"
    assert store.get_job(9999) is None

    pending = store.list_jobs(state="pending")
    assert len(pending) == 2
    assert {j.id for j in pending} == {j1.id, j3.id}

    running = store.list_jobs(state="running")
    assert len(running) == 1
    assert running[0].id == j2.id

    index_jobs = store.list_jobs(kind="index")
    assert len(index_jobs) == 2

    children = store.list_jobs(parent_id=j1.id)
    assert len(children) == 1
    assert children[0].id == j3.id


def test_job_store_updates_progress_payload_error(tmp_path: Path):
    db_path = tmp_path / "test_jobs.db"
    store = JobStore(db_path)

    job = store.create_job(kind="transcribe", title="Transcribe lecture")
    now = time.time()
    updated = store.update_job(
        job.id,
        state="running",
        started=now,
        progress="50% transcribed",
        retries=1,
        error="minor glitch",
        payload={"audio": "lecture.wav", "chunks": 5}
    )
    assert updated is not None
    assert updated.state == "running"
    assert updated.started == now
    assert updated.progress == "50% transcribed"
    assert updated.retries == 1
    assert updated.error == "minor glitch"
    assert updated.payload == {"audio": "lecture.wav", "chunks": 5}

    fetched = store.get_job(job.id)
    assert fetched.progress == "50% transcribed"
    assert fetched.payload["chunks"] == 5


def test_job_store_integrates_with_existing_db(tmp_path: Path):
    # Existing database with other tables
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO memories (text) VALUES ('remember this')")
    conn.commit()
    conn.close()

    store = JobStore(db_path)
    job = store.create_job(kind="maintenance", title="Vacuum DB")
    assert job.id == 1

    # Verify original table still intact
    conn = sqlite3.connect(str(db_path))
    mem = conn.execute("SELECT text FROM memories").fetchone()
    assert mem[0] == "remember this"
    conn.close()
