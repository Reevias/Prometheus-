"""Tests for prometheus.jobs.runner (Item 5.1.3)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
import pytest

from prometheus.jobs.store import JobStore
from prometheus.jobs.states import PENDING, RUNNING, DONE, FAILED, PAUSED, CANCELLED
from prometheus.jobs.runner import JobRunner, start_job, report_progress


def test_start_job_sync_success(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    runner = JobRunner(store)

    completed = []

    def simple_work(x, y):
        completed.append(x + y)
        return x + y

    job = runner.start_job(kind="calc", title="Add numbers", func=simple_work, args=(2, 3))
    assert job.id > 0

    # Wait for completion
    thread = runner.get_thread(job.id)
    if thread:
        thread.join(timeout=5.0)

    final_job = store.get_job(job.id)
    assert final_job.state == DONE
    assert final_job.started > 0
    assert final_job.finished >= final_job.started
    assert final_job.error == ""
    assert completed == [5]


def test_start_job_sync_failure(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    runner = JobRunner(store)

    def failing_work():
        raise RuntimeError("Something exploded")

    job = runner.start_job(kind="flaky", title="Failing task", func=failing_work)
    thread = runner.get_thread(job.id)
    if thread:
        thread.join(timeout=5.0)

    final_job = store.get_job(job.id)
    assert final_job.state == FAILED
    assert "Something exploded" in final_job.error
    assert final_job.finished > 0


def test_start_job_async_coroutine(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    runner = JobRunner(store)

    async def async_calc(val):
        await asyncio.sleep(0.05)
        return val * 2

    async def run_test():
        job = runner.start_job(kind="async_calc", title="Double val", func=async_calc, args=(21,))
        task = runner.get_task(job.id)
        if task:
            await task
        final_job = store.get_job(job.id)
        assert final_job.state == DONE
        assert final_job.finished > 0

    asyncio.run(run_test())


def test_start_job_records_real_progress_only(tmp_path: Path):
    store = JobStore(tmp_path / "jobs.db")
    runner = JobRunner(store)

    def multi_step():
        report_progress("step 1 / 3")
        time.sleep(0.05)
        report_progress("step 2 / 3")
        time.sleep(0.05)
        report_progress("step 3 / 3")

    job = runner.start_job(kind="steps", title="Three step task", func=multi_step)
    thread = runner.get_thread(job.id)
    if thread:
        thread.join(timeout=5.0)

    final_job = store.get_job(job.id)
    assert final_job.state == DONE
    assert final_job.progress == "step 3 / 3"
