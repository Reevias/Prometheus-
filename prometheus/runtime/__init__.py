"""One way to run tasks (M6 step 6.1): every job starts here.

    from prometheus.runtime import start_job, thread_of
    job = start_job("backup", "Backup now", work, name="prometheus-backup")

The job runs on the laptop (LocalRuntime) unless the owner moved it: a JobSpec with another runtime is refused unless
that runtime is set up and the owner's yes (an approval id) checks out. Always-on loops never move.
"""
from __future__ import annotations

import threading
from typing import Callable

from prometheus.runtime.base import ExecutionRuntime, JobSpec, RuntimeRefused
from prometheus.runtime.lint import find_direct_starts
from prometheus.runtime.local import LocalRuntime, build_local


class Runtimes:
    """The runtimes this Prometheus has, by name. "local" is always there and is the default."""

    def __init__(self, local: LocalRuntime, approval_check: Callable[[str, JobSpec], bool] | None = None):
        self.local = local
        self._all: dict[str, ExecutionRuntime] = {"local": local}
        # checks the owner's yes for a move; until Cloudroom's approvals are wired (6.2) every move is refused
        self.approval_check: Callable[[str, JobSpec], bool] = approval_check or (lambda approval, spec: False)

    def register(self, runtime: ExecutionRuntime) -> None:
        if not runtime.name or runtime.name == "local":
            raise ValueError("A runtime needs its own name (not 'local').")
        self._all[runtime.name] = runtime

    def get(self, name: str) -> ExecutionRuntime | None:
        return self._all.get(name)

    def start(self, spec: JobSpec):
        """Start a job where the spec says, after the checks; the laptop by default."""
        where = spec.runtime or "local"
        if where == "local":
            return self.local.start(spec)
        if spec.service:
            raise RuntimeRefused("Always-on loops (reminders, sync, ...) always run on the laptop.")
        runtime = self._all.get(where)
        if runtime is None:
            raise RuntimeRefused(f"'{where}' isn't set up, so the task stays on the laptop.")
        check = getattr(runtime, "check_yes", None) or self.approval_check    # the runtime's own approvals first
        if not spec.approval or not check(spec.approval, spec):
            raise RuntimeRefused(f"Moving '{spec.title}' to {where} needs your yes first.")
        return runtime.start(spec)


_RUNTIMES: Runtimes | None = None
_LOCK = threading.RLock()


def get_runtimes() -> Runtimes:
    """The app's runtimes (a laptop-only one on the default job runner if the app hasn't set them yet)."""
    global _RUNTIMES
    with _LOCK:
        if _RUNTIMES is None:
            _RUNTIMES = Runtimes(LocalRuntime())
        return _RUNTIMES


def set_runtimes(runtimes: Runtimes | None) -> None:
    global _RUNTIMES
    with _LOCK:
        _RUNTIMES = runtimes


def setup_local(job_store) -> Runtimes:
    """The app's runtimes on its job tracker: one job runner, set as the default, and made the app's runtimes."""
    runtimes = Runtimes(build_local(job_store))
    set_runtimes(runtimes)
    return runtimes


def start_job(kind: str, title: str, func: Callable, args: tuple = (), kwargs: dict | None = None,
              parent_id: int | None = None, payload: dict | None = None, name: str | None = None,
              service: bool = False, runtime: str = "local", approval: str | None = None):
    """Start a job through the app's runtimes (the laptop unless moved with the owner's yes)."""
    return get_runtimes().start(JobSpec(kind, title, func=func, args=args, kwargs=kwargs or {}, payload=payload,
                                        parent_id=parent_id, name=name, service=service, runtime=runtime,
                                        approval=approval))


def thread_of(job) -> threading.Thread | None:
    """The thread a laptop job runs in (for callers that wait on it)."""
    return get_runtimes().local.thread_of(job.id if hasattr(job, "id") else int(job))


__all__ = ["ExecutionRuntime", "JobSpec", "LocalRuntime", "RuntimeRefused", "Runtimes", "find_direct_starts",
           "get_runtimes", "set_runtimes", "setup_local", "start_job", "thread_of"]
