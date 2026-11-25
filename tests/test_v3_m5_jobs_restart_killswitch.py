"""Tests for startup recovery and killswitch integration (Items 5.1.4 and 5.1.5)."""
from __future__ import annotations

from pathlib import Path
import pytest

from prometheus.jobs.store import JobStore
from prometheus.jobs.states import PENDING, RUNNING, DONE, FAILED, PAUSED, CANCELLED
from prometheus.safety.killswitch import KillSwitch


def test_startup_recovers_running_jobs_to_paused(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)

    # Setup jobs in various states before a simulated crash/kill
    j_pending = store.create_job(kind="calc", title="Pending Job", state=PENDING)
    j_running1 = store.create_job(kind="agent", title="Active Agent 1", state=RUNNING)
    j_running2 = store.create_job(kind="index", title="Active Index 2", state=RUNNING)
    j_done = store.create_job(kind="sync", title="Finished Sync", state=DONE)
    j_failed = store.create_job(kind="web", title="Failed Request", state=FAILED)

    # Simulate restart by calling recover_after_restart on startup
    recovered_count = store.recover_after_restart()
    assert recovered_count == 2

    # Verify running jobs became paused with the exact reason
    r1 = store.get_job(j_running1.id)
    assert r1.state == PAUSED
    assert r1.error == "Prometheus restarted"

    r2 = store.get_job(j_running2.id)
    assert r2.state == PAUSED
    assert r2.error == "Prometheus restarted"

    # Verify other jobs are untouched
    assert store.get_job(j_pending.id).state == PENDING
    assert store.get_job(j_done.id).state == DONE
    assert store.get_job(j_failed.id).state == FAILED


def test_killswitch_pauses_all_jobs_and_resume_keeps_them_paused(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    store = JobStore(db_path)
    killswitch = KillSwitch()

    # Register store pause handler on killswitch
    killswitch.on_stop(lambda reason: store.pause_all(f"Stopped: {reason}"))

    j1 = store.create_job(kind="build", title="Build 1", state=RUNNING)
    j2 = store.create_job(kind="build", title="Build 2", state=RUNNING)
    j3 = store.create_job(kind="build", title="Build 3", state=PENDING)

    # Trigger Stop all
    killswitch.trigger("Emergency Stop")
    assert killswitch.is_set is True

    # Every running job is now paused
    assert store.get_job(j1.id).state == PAUSED
    assert "Emergency Stop" in store.get_job(j1.id).error
    assert store.get_job(j2.id).state == PAUSED
    assert "Emergency Stop" in store.get_job(j2.id).error

    # Reset (Resume all) lifts the stop, but jobs STAY paused until individually resumed
    killswitch.reset()
    assert killswitch.is_set is False

    # Jobs are still paused!
    assert store.get_job(j1.id).state == PAUSED
    assert store.get_job(j2.id).state == PAUSED

    # Now individual resume moves to pending
    store.set_state(j1.id, PENDING)
    assert store.get_job(j1.id).state == PENDING
    assert store.get_job(j2.id).state == PAUSED
