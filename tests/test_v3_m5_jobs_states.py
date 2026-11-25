"""Tests for prometheus.jobs.states (Item 5.1.2)."""
from __future__ import annotations

from pathlib import Path
import pytest

from prometheus.jobs.states import (
    PENDING, RUNNING, DONE, FAILED, CANCELLED, PAUSED,
    ALL_STATES,
    InvalidStateTransition,
    validate_transition,
    can_transition,
)
from prometheus.jobs.store import JobStore


def test_valid_state_transitions():
    # pending -> running
    assert can_transition(PENDING, RUNNING) is True
    validate_transition(PENDING, RUNNING)

    # running -> done / failed / cancelled / paused
    for target in (DONE, FAILED, CANCELLED, PAUSED):
        assert can_transition(RUNNING, target) is True
        validate_transition(RUNNING, target)

    # paused -> pending (on Resume)
    assert can_transition(PAUSED, PENDING) is True
    validate_transition(PAUSED, PENDING)

    # pending -> cancelled, paused -> cancelled
    assert can_transition(PENDING, CANCELLED) is True
    assert can_transition(PAUSED, CANCELLED) is True


def test_invalid_state_transitions_raise_error():
    # Cannot jump from pending to done/failed/paused directly
    for target in (DONE, FAILED, PAUSED):
        assert can_transition(PENDING, target) is False
        with pytest.raises(InvalidStateTransition):
            validate_transition(PENDING, target)

    # Cannot jump from running back to pending
    assert can_transition(RUNNING, PENDING) is False
    with pytest.raises(InvalidStateTransition):
        validate_transition(RUNNING, PENDING)

    # Paused cannot jump straight to running without moving to pending first
    assert can_transition(PAUSED, RUNNING) is False
    with pytest.raises(InvalidStateTransition):
        validate_transition(PAUSED, RUNNING)

    # Terminal states (done, failed, cancelled) cannot move anywhere
    for terminal in (DONE, FAILED, CANCELLED):
        for target in ALL_STATES:
            assert can_transition(terminal, target) is False
            with pytest.raises(InvalidStateTransition):
                validate_transition(terminal, target)

    # Unknown state
    with pytest.raises(InvalidStateTransition):
        validate_transition("unknown", RUNNING)
    with pytest.raises(InvalidStateTransition):
        validate_transition(PENDING, "unknown")


def test_job_store_enforces_state_transitions(tmp_path: Path):
    db_path = tmp_path / "test_jobs.db"
    store = JobStore(db_path)

    job = store.create_job(kind="agent", title="Test task")
    assert job.state == PENDING

    # Move to running
    job = store.set_state(job.id, RUNNING)
    assert job.state == RUNNING

    # Move to paused
    job = store.set_state(job.id, PAUSED)
    assert job.state == PAUSED

    # Resume: paused -> pending
    job = store.set_state(job.id, PENDING)
    assert job.state == PENDING

    # Move to running
    job = store.set_state(job.id, RUNNING)
    assert job.state == RUNNING

    # Complete: running -> done
    job = store.set_state(job.id, DONE)
    assert job.state == DONE

    # Attempt invalid move: done -> running
    with pytest.raises(InvalidStateTransition):
        store.set_state(job.id, RUNNING)
