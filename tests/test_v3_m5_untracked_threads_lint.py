"""Lint test enforcing that no untracked raw threads or asyncio tasks exist (Item 5.1.8)."""
from __future__ import annotations

from pathlib import Path

from prometheus.jobs.lint import find_untracked


def test_no_untracked_raw_threads_or_tasks():
    root = Path(__file__).resolve().parent.parent
    violations = find_untracked(root)
    assert not violations, (
        f"Found {len(violations)} untracked raw thread/task calls outside allow-list:\n" + "\n".join(violations)
    )
