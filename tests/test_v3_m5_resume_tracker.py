"""Tests for moving resume.py onto the job tracker (Item 5.1.6)."""
from __future__ import annotations

import time
from pathlib import Path
import pytest

from prometheus.memory.store import Store
from prometheus.jobs.store import JobStore
from prometheus.jobs.states import PENDING, RUNNING, DONE, FAILED, PAUSED, CANCELLED
from prometheus import resume


def test_resume_note_limit_creates_and_updates_job_in_tracker(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    store = Store(db_path)
    job_store = JobStore(store)

    # First limit hit: creates job in tracker
    msg = resume.note_limit(store, chat_id=42, text="Calculate pi", error="rate limit exceeded", now=1000.0)
    assert "6 minutes" in msg

    jobs = job_store.list_jobs(kind="resume")
    assert len(jobs) == 1
    j = jobs[0]
    assert j.kind == "resume"
    assert "Calculate pi" in j.title
    assert j.payload["chat_id"] == 42
    assert j.payload["text"] == "Calculate pi"
    assert j.retries == 1
    assert j.payload["due"] == 1000.0 + 6 * 60

    # Second limit hit: updates the existing job instead of creating a second one
    msg2 = resume.note_limit(store, chat_id=42, text="Calculate pi", error="rate limit exceeded", now=1400.0)
    assert "15 minutes" in msg2

    jobs = job_store.list_jobs(kind="resume")
    assert len(jobs) == 1
    assert jobs[0].id == j.id
    assert jobs[0].retries == 2
    assert jobs[0].payload["due"] == 1400.0 + 15 * 60


def test_resume_cancel_cancels_jobs_in_tracker(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    store = Store(db_path)
    job_store = JobStore(store)

    resume.note_limit(store, chat_id=10, text="task 1", error="quota exceeded", now=100.0)
    resume.note_limit(store, chat_id=20, text="task 2", error="quota exceeded", now=100.0)

    assert len(job_store.list_jobs(kind="resume")) == 2

    # Cancel chat 10
    n = resume.cancel(store, chat_id=10)
    assert n == 1

    jobs10 = [j for j in job_store.list_jobs(kind="resume") if j.payload.get("chat_id") == 10]
    assert len(jobs10) == 1
    assert jobs10[0].state == CANCELLED

    jobs20 = [j for j in job_store.list_jobs(kind="resume") if j.payload.get("chat_id") == 20]
    assert len(jobs20) == 1
    assert jobs20[0].state in (PENDING, PAUSED)
