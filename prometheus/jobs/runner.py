"""Job execution runner for Prometheus M5 (Item 5.1.3)."""
from __future__ import annotations

import asyncio
import contextvars
import inspect
import logging
import threading
import weakref
from collections import deque
from typing import Any, Callable

from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING
from prometheus.jobs.store import Job, JobStore

log = logging.getLogger(__name__)

_CURRENT_JOB_ID: contextvars.ContextVar[int | None] = contextvars.ContextVar("_CURRENT_JOB_ID", default=None)
_CURRENT_JOB_RUNNER: contextvars.ContextVar[Any | None] = contextvars.ContextVar("_CURRENT_JOB_RUNNER", default=None)


def current_job_id() -> int | None:
    """Return the ID of the currently executing job in this context, if any."""
    return _CURRENT_JOB_ID.get()


def report_progress(text: str) -> None:
    """Report progress text for the currently executing job."""
    jid = _CURRENT_JOB_ID.get()
    runner = _CURRENT_JOB_RUNNER.get()
    if jid is not None and runner is not None and runner.store is not None:
        runner.store.update_job(jid, progress=str(text))


class JobRunner:
    """Tracks and executes jobs in background threads or asyncio tasks."""

    def __init__(self, store: JobStore | None = None):
        self.store = store if store is not None else JobStore()
        self._threads: dict[int, threading.Thread] = {}
        self._all_threads: weakref.WeakValueDictionary[int, threading.Thread] = weakref.WeakValueDictionary()
        self._recent: deque[threading.Thread] = deque(maxlen=64)   # callers fetch a fast job's thread just after start
        self._tasks: dict[int, asyncio.Task] = {}
        self._lock = threading.RLock()

    def get_thread(self, job_id: int) -> threading.Thread | None:
        with self._lock:
            return self._all_threads.get(job_id) or self._threads.get(job_id)

    def get_task(self, job_id: int) -> asyncio.Task | None:
        with self._lock:
            return self._tasks.get(job_id)

    def start_job(self, kind: str, title: str, func: Callable,
                  args: tuple = (), kwargs: dict | None = None,
                  parent_id: int | None = None, payload: dict | None = None,
                  name: str | None = None, service: bool = False) -> Job:
        """Create a job record and launch it in a tracked thread or asyncio task. `service` marks a loop that
        Prometheus itself starts at every launch (reminders, sync, ...): Stop all and restarts don't pause it."""
        kwargs = kwargs or {}
        if service:
            payload = {**(payload or {}), "service": True}
        job = self.store.create_job(kind=kind, title=title, parent_id=parent_id, payload=payload, state=PENDING)
        job_id = job.id

        is_async = inspect.iscoroutinefunction(func) or inspect.iscoroutine(func)

        if is_async:
            self._start_async_job(job_id, func, args, kwargs)
        else:
            self._start_sync_job(job_id, func, args, kwargs, name=name or f"job-{job_id}-{kind}")

        return self.store.get_job(job_id) or job

    def run_existing(self, job_id: int, func: Callable, args: tuple = (), kwargs: dict | None = None,
                     name: str | None = None, mark_running: bool = True) -> threading.Thread | None:
        """Run work for a job that is already in the tracker (a graph step, a resumed job). With
        mark_running=False the caller has already moved it to running (and records its end itself)."""
        self._start_sync_job(job_id, func, args, kwargs or {}, name=name or f"job-{job_id}", mark_running=mark_running)
        return self.get_thread(job_id)

    def _start_sync_job(self, job_id: int, func: Callable, args: tuple, kwargs: dict, name: str,
                        mark_running: bool = True) -> None:
        def _wrapper():
            _CURRENT_JOB_ID.set(job_id)
            _CURRENT_JOB_RUNNER.set(self)
            if mark_running:
                try:
                    self.store.set_state(job_id, RUNNING)
                except Exception as err:
                    log.exception("Failed to transition job %d to RUNNING: %s", job_id, err)
                    return

            try:
                func(*args, **kwargs)
                if mark_running:                                       # else the caller records how it ended
                    self._record_end(job_id, None)
            except Exception as exc:
                log.exception("Job %d failed: %s", job_id, exc)
                if mark_running:
                    self._record_end(job_id, exc)
            finally:
                with self._lock:
                    self._threads.pop(job_id, None)

        thread = threading.Thread(target=_wrapper, name=name, daemon=True)
        with self._lock:
            self._threads[job_id] = thread
            self._all_threads[job_id] = thread
            self._recent.append(thread)
        thread.start()

    def _record_end(self, job_id: int, exc: BaseException | None) -> None:
        """How the work really ended. A job paused while it ran (Stop all, or by hand) that then finishes is not left
        "paused" for ever (M5 audit F2): after Stop all it is cancelled ("Stopped by user"), otherwise done or
        failed. A graph's own job stays paused: its steps carry on with Resume."""
        with self.store._lock:
            job = self.store.get_job(job_id)
            if job is None or job.state in (DONE, FAILED, CANCELLED):
                return
            if job.state == RUNNING:
                if exc is None:
                    self.store.set_state(job_id, DONE)
                else:
                    self.store.set_state(job_id, FAILED, error=str(exc))
                return
            if job.payload.get("graph"):
                return
            if (job.error or "").startswith("Stopped by user"):
                self.store.finish(job_id, CANCELLED, error=job.error)
            else:
                self.store.finish(job_id, FAILED if exc is not None else DONE,
                                  error=str(exc) if exc is not None else None)

    def _start_async_job(self, job_id: int, func: Callable, args: tuple, kwargs: dict) -> None:
        async def _async_wrapper():
            _CURRENT_JOB_ID.set(job_id)
            _CURRENT_JOB_RUNNER.set(self)
            try:
                self.store.set_state(job_id, RUNNING)
            except Exception as err:
                log.exception("Failed to transition job %d to RUNNING: %s", job_id, err)
                return

            try:
                if inspect.iscoroutine(func):
                    await func
                else:
                    res = func(*args, **kwargs)
                    if inspect.iscoroutine(res):
                        await res
                job = self.store.get_job(job_id)
                if job and job.state == RUNNING:
                    self.store.set_state(job_id, DONE)
            except asyncio.CancelledError:
                job = self.store.get_job(job_id)
                if job and job.state == RUNNING:
                    self.store.set_state(job_id, CANCELLED, error="Task cancelled")
                raise
            except Exception as exc:
                log.exception("Async job %d failed: %s", job_id, exc)
                job = self.store.get_job(job_id)
                if job and job.state == RUNNING:
                    self.store.set_state(job_id, FAILED, error=str(exc))
            finally:
                with self._lock:
                    self._tasks.pop(job_id, None)
                    self._threads.pop(job_id, None)     # when it ran in its own thread (no loop was running)

        try:
            loop = asyncio.get_running_loop()
            task = loop.create_task(_async_wrapper())
            with self._lock:
                self._tasks[job_id] = task
        except RuntimeError:
            # No running event loop in current thread: launch in new thread with new loop
            def _runner_thread():
                asyncio.run(_async_wrapper())
            thread = threading.Thread(target=_runner_thread, name=f"job-async-{job_id}", daemon=True)
            with self._lock:
                self._threads[job_id] = thread
                self._all_threads[job_id] = thread
                self._recent.append(thread)
            thread.start()

    # The buttons below check and change a job under the database's lock only, and touch the runner's own lock
    # separately: a graph driver holds the database lock while it starts a step (which needs the runner's lock), so
    # holding both here in the other order could deadlock (M5 audit A3).
    def cancel_job(self, job_id: int, reason: str = "Cancelled by user") -> bool:
        """Cancel a running or pending job."""
        with self.store._lock:
            job = self.store.get_job(job_id)
            if not job or job.state in (DONE, FAILED, CANCELLED):
                return False
            self.store.set_state(job_id, CANCELLED, error=reason)
        with self._lock:
            task = self._tasks.get(job_id)
        if task and not task.done():
            task.cancel()
        return True

    def pause_job(self, job_id: int, reason: str = "Paused by user") -> bool:
        """Pause a running job."""
        with self.store._lock:
            job = self.store.get_job(job_id)
            if not job or job.state != RUNNING:
                return False
            self.store.set_state(job_id, PAUSED, error=reason)
            return True

    def resume_job(self, job_id: int) -> bool:
        """Resume a paused job by transitioning to pending."""
        with self.store._lock:
            job = self.store.get_job(job_id)
            if not job or job.state != PAUSED:
                return False
            self.store.set_state(job_id, PENDING)
            return True


_DEFAULT_RUNNER: JobRunner | None = None
_DEFAULT_RUNNER_LOCK = threading.RLock()


def get_default_runner() -> JobRunner:
    """Return the global default JobRunner instance."""
    global _DEFAULT_RUNNER
    with _DEFAULT_RUNNER_LOCK:
        if _DEFAULT_RUNNER is None:
            _DEFAULT_RUNNER = JobRunner()
        return _DEFAULT_RUNNER


def set_default_runner(runner: JobRunner | None) -> None:
    """Set the global default JobRunner instance."""
    global _DEFAULT_RUNNER
    with _DEFAULT_RUNNER_LOCK:
        _DEFAULT_RUNNER = runner


def start_job(kind: str, title: str, func: Callable,
              args: tuple = (), kwargs: dict | None = None,
              parent_id: int | None = None, payload: dict | None = None,
              name: str | None = None, store: JobStore | None = None, service: bool = False) -> Job:
    """Convenience function to start a job using the default or provided store."""
    if store is not None:
        runner = JobRunner(store)
    else:
        runner = get_default_runner()
    return runner.start_job(kind=kind, title=title, func=func, args=args,
                            kwargs=kwargs, parent_id=parent_id,
                            payload=payload, name=name, service=service)
