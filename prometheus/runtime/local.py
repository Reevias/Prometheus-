"""LocalRuntime (M6 step 6.1): the laptop. It wraps M5's job runner, task graphs and limits with no change in what they
do; it adds only the five runtime calls and the job's result."""
from __future__ import annotations

import functools
import inspect
import threading
from collections import OrderedDict
from typing import Any

from prometheus.jobs import limits
from prometheus.jobs.graph import GraphRunner
from prometheus.jobs.runner import JobRunner, current_job_id, get_default_runner, set_default_runner
from prometheus.jobs.states import TERMINAL_STATES
from prometheus.jobs.store import JobStore
from prometheus.runtime.base import ExecutionRuntime, JobSpec

RESULTS_KEPT = 256          # results of the latest jobs kept in memory (a graph step's result is in its record)


class LocalRuntime(ExecutionRuntime):
    name = "local"

    def __init__(self, store: JobStore | None = None, runner: JobRunner | None = None,
                 graphs: GraphRunner | None = None):
        self._runner = runner or (graphs.runner if graphs is not None else None)
        self._store = store or (graphs.store if graphs is not None else None)
        self._graphs = graphs
        self._results: OrderedDict[int, Any] = OrderedDict()
        self._lock = threading.Lock()

    # -- what it wraps ----------------------------------------------------------------------------------------------
    @property
    def runner(self) -> JobRunner:
        """The job runner: its own, or the app's default one (read each time, so the app can set it later)."""
        if self._runner is not None:
            return self._runner
        if self._store is not None:
            self._runner = JobRunner(self._store)
            return self._runner
        return get_default_runner()

    @property
    def store(self) -> JobStore:
        return self._store if self._store is not None else self.runner.store

    @property
    def graphs(self) -> GraphRunner:
        if self._graphs is None:
            self._graphs = GraphRunner(self.store, self.runner)          # M5's RAM-aware child limit by default
        return self._graphs

    def make_graphs(self, killswitch=None) -> GraphRunner:
        """The app's graph runner (with Stop all wired in)."""
        self._graphs = GraphRunner(self.store, self.runner, killswitch=killswitch)
        return self._graphs

    # -- the five calls ---------------------------------------------------------------------------------------------
    def start(self, spec: JobSpec):
        func = self._keeping_result(spec.func)
        return self.runner.start_job(kind=spec.kind, title=spec.title, func=func, args=tuple(spec.args),
                                     kwargs=dict(spec.kwargs or {}), parent_id=spec.parent_id, payload=spec.payload,
                                     name=spec.name, service=spec.service)

    def status(self, job_id: int):
        return self.store.get_job(job_id)

    def cancel(self, job_id: int, reason: str = "Cancelled by you") -> bool:
        job = self.store.get_job(job_id)
        if job is None or job.state in TERMINAL_STATES:
            return False
        if job.payload.get("graph"):
            return self.graphs.cancel(job_id, reason)
        done = self.runner.cancel_job(job_id, reason)
        limits.end_job(job_id)                                   # its programs end with it (M5 5.3)
        return done

    def events(self, job_id: int, after: int = 0) -> list[dict]:
        return self.store.events(job_id, after)

    def result(self, job_id: int) -> Any:
        with self._lock:
            if job_id in self._results:
                return self._results[job_id]
        job = self.store.get_job(job_id)
        return job.payload.get("result") if job is not None else None

    # -- extras the app uses ----------------------------------------------------------------------------------------
    def thread_of(self, job_id: int) -> threading.Thread | None:
        return self.runner.get_thread(job_id)

    def start_graph(self, title: str, steps: list[dict], kind: str = "graph", payload: dict | None = None) -> int:
        """Make a task graph (M5 5.2 checks) and start it; returns the graph's job id."""
        gid = self.graphs.create(title, steps, kind=kind, payload=payload)
        self.graphs.start(gid)
        return gid

    def _keeping_result(self, func):
        """The same work, with its return value kept for result() (the work itself is untouched)."""
        if func is None or inspect.iscoroutine(func):
            return func

        def keep(value):
            jid = current_job_id()
            if jid is not None:
                with self._lock:
                    self._results[jid] = value
                    while len(self._results) > RESULTS_KEPT:
                        self._results.popitem(last=False)
            return value

        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def run_async(*a, **k):
                return keep(await func(*a, **k))
            return run_async

        @functools.wraps(func)
        def run(*a, **k):
            return keep(func(*a, **k))
        return run


def build_local(job_store: JobStore) -> LocalRuntime:
    """The app's laptop runtime: one job runner for the whole app (also the default for code that runs before)."""
    runner = JobRunner(job_store)
    set_default_runner(runner)
    return LocalRuntime(job_store, runner)
