"""State definitions and transition validation for Prometheus jobs (M5 Item 5.1.2)."""
from __future__ import annotations

PENDING = "pending"
RUNNING = "running"
DONE = "done"
FAILED = "failed"
CANCELLED = "cancelled"
PAUSED = "paused"

ALL_STATES = frozenset({PENDING, RUNNING, DONE, FAILED, CANCELLED, PAUSED})
TERMINAL_STATES = frozenset({DONE, FAILED, CANCELLED})

# Allowed moves only (M5 5.1.2):
# pending -> running (or cancelled)
# running -> done, failed, cancelled, paused
# paused -> pending (on Resume) or cancelled
# Any other move raises an error.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    PENDING: frozenset({RUNNING, CANCELLED}),
    RUNNING: frozenset({DONE, FAILED, CANCELLED, PAUSED}),
    PAUSED: frozenset({PENDING, CANCELLED}),
    DONE: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
}


class InvalidStateTransition(ValueError):
    """Raised when an illegal job state transition is attempted."""
    pass


def can_transition(from_state: str, to_state: str) -> bool:
    """Return True if transitioning from from_state to to_state is allowed."""
    if from_state not in ALL_STATES or to_state not in ALL_STATES:
        return False
    return to_state in ALLOWED_TRANSITIONS.get(from_state, frozenset())


def validate_transition(from_state: str, to_state: str) -> None:
    """Validate state transition or raise InvalidStateTransition."""
    if from_state not in ALL_STATES:
        raise InvalidStateTransition(f"Unknown initial state: {from_state!r}")
    if to_state not in ALL_STATES:
        raise InvalidStateTransition(f"Unknown target state: {to_state!r}")
    if not can_transition(from_state, to_state):
        allowed = sorted(ALLOWED_TRANSITIONS.get(from_state, set()))
        raise InvalidStateTransition(
            f"Invalid transition: {from_state!r} -> {to_state!r}. Allowed moves: {allowed}"
        )
