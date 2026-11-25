"""One way to run tasks (M6 step 6.1).

Every job in Prometheus is started through an ExecutionRuntime. LocalRuntime (the laptop) is the default and runs
M5's job runner, task graphs and limits unchanged. CloudroomRuntime (6.2) plugs in behind the same five calls; a job
only goes there when the owner said yes (Runtimes.start checks it in code).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar


class RuntimeRefused(PermissionError):
    """The job was not started; the message says why in plain words."""


@dataclass
class JobSpec:
    """What to run and where. `runtime` is "local" unless the owner moved it; `approval` is the id of the owner's yes."""
    kind: str
    title: str
    func: Callable | None = None
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)
    payload: dict | None = None
    parent_id: int | None = None
    name: str | None = None
    service: bool = False
    runtime: str = "local"
    approval: str | None = None


class ExecutionRuntime(ABC):
    """Start a job, see its status, cancel it, read its numbered events and its result."""
    name: ClassVar[str] = ""

    @abstractmethod
    def start(self, spec: JobSpec):
        """Start the job; returns its tracker record (prometheus.jobs.store.Job)."""

    @abstractmethod
    def status(self, job_id: int):
        """The job's tracker record now, or None."""

    @abstractmethod
    def cancel(self, job_id: int, reason: str = "Cancelled by you") -> bool:
        """Cancel it (and what it started). False when it had already finished."""

    @abstractmethod
    def events(self, job_id: int, after: int = 0) -> list[dict]:
        """Its events after number `after`, in order: {seq, at, state, text}."""

    @abstractmethod
    def result(self, job_id: int) -> Any:
        """What the job's work returned (None when nothing or not known)."""
