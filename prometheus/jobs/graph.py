"""Task graphs (M5 step 5.2): a graph is a parent job; its steps are child jobs with a "needs" list.

- Made only after checks: at least one step, at most MAX_STEPS, ids used once, every need exists, no loops, every
  step has a known kind (or, in this run only, a function).
- Every step whose needs are done runs; independent steps run side by side, never more than the slots allow.
- A failing step is tried again (default 2 more times, waits of 10 s then 60 s). The count is saved in the
  tracker, so a restart doesn't reset it. Its slot is free while it waits.
- A failed step cancels the steps that need it (and theirs); other branches carry on. The graph then ends failed.
- Cancel and pause spread to the steps. A running step is told to stop (StepContext.should_stop/wait); Python
  threads can't be killed, so a step that never checks finishes its current try and its result is thrown away.
- After a restart the whole graph is paused (nothing runs by itself); Resume carries on from the finished steps.
  A graph with in-memory functions can't carry on in a new run: it is closed with a plain reason.
"""
from __future__ import annotations

import contextvars
import json
import logging
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable

from prometheus.jobs.runner import JobRunner
from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING, TERMINAL_STATES
from prometheus.jobs.store import Job, JobStore

log = logging.getLogger(__name__)

MAX_STEPS = 50
DEFAULT_RETRIES = 2
RETRY_WAITS = (10, 60)                 # seconds before the 1st and 2nd retry (later ones reuse the last)
GRAPH_KIND = "graph"
STEP_KIND = "graph_step"
NEED_FAILED = "Cancelled (a step it needs failed)"
NEED_CANCELLED = "Cancelled (a step it needs was cancelled)"
NOT_RESUMABLE = "Prometheus restarted, so this can't carry on by itself: run it again."

_KINDS: dict[str, Callable] = {}


def step_kind(name: str):
    """Register a step function by name, so a saved graph can carry on after a restart."""
    def deco(func: Callable) -> Callable:
        _KINDS[name] = func
        return func
    return deco


class GraphError(ValueError):
    """The graph was refused when it was made (nothing is saved)."""


class GraphFailed(RuntimeError):
    """Raised by the graph's own job when a step failed (the tracker then marks the graph failed)."""


class Slots:
    """How many steps may run at once. M5 5.3 replaces this with the RAM-aware limit (prometheus.jobs.limits)."""

    def __init__(self, size: int = 2):
        self.size = size
        self._used = 0
        self._lock = threading.Lock()
        self.reason = ""

    def try_acquire(self) -> bool:
        with self._lock:
            if self._used >= self.size:
                self.reason = "waiting for a free slot"
                return False
            self._used += 1
            return True

    def release(self) -> None:
        with self._lock:
            self._used = max(0, self._used - 1)

    @property
    def used(self) -> int:
        return self._used


@dataclass
class StepContext:
    """What a step function gets: its needs' results and a way to notice it should stop."""
    graph_id: int
    job_id: int
    step: str
    attempt: int
    results: dict = field(default_factory=dict)
    retries: int = 0
    _runner: Any = None

    def should_stop(self) -> bool:
        return self._runner._stale(self.job_id, self.attempt)

    def wait(self, seconds: float) -> bool:
        """Sleep up to `seconds`; returns early (True) as soon as the step should stop."""
        end = time.monotonic() + max(0.0, seconds)
        wake = self._runner._wake[self.graph_id]
        while True:
            if self.should_stop():
                return True
            left = end - time.monotonic()
            if left <= 0:
                return False
            wake.wait(min(0.1, left))

    def progress(self, text: str) -> None:
        if not self.should_stop():
            self._runner.store.update_job(self.job_id, progress=str(text)[:500])


def _check(steps: list[dict]) -> list[dict]:
    if not isinstance(steps, list) or not steps:
        raise GraphError("A graph needs steps, and this one has no steps.")
    if len(steps) > MAX_STEPS:
        raise GraphError(f"A graph can have at most {MAX_STEPS} steps (this one has {len(steps)}).")
    clean, seen = [], set()
    for s in steps:
        if not isinstance(s, dict) or s.get("id") in (None, ""):
            raise GraphError("Every step needs an id.")
        sid = str(s["id"])
        if sid in seen:
            raise GraphError(f"Step {sid} appears twice.")
        seen.add(sid)
        func, kind = s.get("func"), str(s.get("kind") or "")
        if func is None and kind not in _KINDS:
            raise GraphError(f"Step {sid} has an unknown kind: {kind!r}.")
        if func is not None and not callable(func):
            raise GraphError(f"Step {sid}: func must be a function.")
        retries = s.get("retries", DEFAULT_RETRIES)
        if not isinstance(retries, int) or not 0 <= retries <= 5:
            raise GraphError(f"Step {sid}: retries must be a number from 0 to 5.")
        needs = s.get("needs") or []
        if not isinstance(needs, (list, tuple)):
            raise GraphError(f"Step {sid}: needs must be a list.")
        clean.append({"id": sid, "kind": kind or "function", "func": func, "title": str(s.get("title") or sid)[:200],
                      "inputs": s.get("inputs") or {}, "needs": [str(n) for n in needs], "retries": retries})
    for s in clean:
        for n in s["needs"]:
            if n not in seen:
                raise GraphError(f"Step {s['id']} needs a missing step: {n}.")
    # loops: take away steps with no unmet needs until none are left (anything left is in a loop)
    left = {s["id"]: set(s["needs"]) for s in clean}
    while left:
        free = [k for k, v in left.items() if not v]
        if not free:
            raise GraphError(f"These steps wait on each other in a loop: {', '.join(sorted(left))}.")
        for k in free:
            del left[k]
        for v in left.values():
            v.difference_update(free)
    return clean


class GraphRunner:
    """Makes, runs, pauses, resumes and cancels graphs. One per Prometheus run (tests make their own)."""

    def __init__(self, store: JobStore, runner: JobRunner | None = None, slots: Slots | None = None,
                 waits: tuple = RETRY_WAITS, max_parallel: int = 2, killswitch=None):
        self.store = store
        self.runner = runner or JobRunner(store)
        self.slots = slots or Slots(max_parallel)
        self.waits = tuple(waits) or RETRY_WAITS
        self.killswitch = killswitch
        self._funcs: dict[tuple[int, str], Callable] = {}
        self._contexts: dict[int, contextvars.Context] = {}
        self._wake: defaultdict[int, threading.Event] = defaultdict(threading.Event)
        self._live: defaultdict[int, set[threading.Thread]] = defaultdict(set)
        self._lock = threading.RLock()

    # -- making ----------------------------------------------------------------------------------------------
    def create(self, title: str, steps: list[dict], kind: str = GRAPH_KIND, payload: dict | None = None) -> int:
        clean = _check(steps)
        resumable = all(s["func"] is None for s in clean)
        with self.store._lock:                       # the whole graph appears at once
            parent = self.store.create_job(kind=kind, title=str(title)[:200],
                                           payload={**(payload or {}), "graph": True, "resumable": resumable})
            for s in clean:
                self.store.create_job(kind=STEP_KIND, title=s["title"], parent_id=parent.id, payload={
                    "step": s["id"], "step_kind": s["kind"], "inputs": s["inputs"], "needs": s["needs"],
                    "max_retries": s["retries"], "attempt": 0, "result": None})
                if s["func"] is not None:
                    self._funcs[(parent.id, s["id"])] = s["func"]
        self._contexts[parent.id] = contextvars.copy_context()      # the caller's model/budget settings
        return parent.id

    # -- the owner's buttons ---------------------------------------------------------------------------------
    def start(self, graph_id: int) -> None:
        self.runner.run_existing(graph_id, self._drive, args=(graph_id,), name=f"prometheus-graph-{graph_id}")

    def cancel(self, graph_id: int, reason: str = "Cancelled by you") -> bool:
        with self.store._lock:
            parent = self.store.get_job(graph_id)
            if parent is None or parent.state in TERMINAL_STATES:
                return False
            for j in self.store.list_jobs(parent_id=graph_id):
                if j.state not in TERMINAL_STATES:
                    self.store.set_state(j.id, CANCELLED, error=reason)
            self.store.set_state(graph_id, CANCELLED, error=reason)
        self._wake[graph_id].set()
        return True

    def pause(self, graph_id: int, reason: str = "Paused by you") -> bool:
        with self.store._lock:
            parent = self.store.get_job(graph_id)
            if parent is None or parent.state != RUNNING:
                return False
            for j in self.store.list_jobs(parent_id=graph_id):
                if j.state == RUNNING:
                    self.store.set_state(j.id, PAUSED, error=reason)
            self.store.set_state(graph_id, PAUSED, error=reason)
        self._wake[graph_id].set()
        return True

    def resume(self, graph_id: int) -> bool:
        with self.store._lock:
            parent = self.store.get_job(graph_id)
            if parent is None or parent.state != PAUSED:
                return False
            if not parent.payload.get("resumable", True) and not any(k[0] == graph_id for k in self._funcs):
                self.cancel(graph_id, NOT_RESUMABLE)
                return False
            for j in self.store.list_jobs(parent_id=graph_id):
                if j.state == PAUSED:
                    self.store.set_state(j.id, PENDING)
            self.store.set_state(graph_id, PENDING, progress="")
        self._wake[graph_id].clear()
        self.start(graph_id)
        return True

    def wait(self, graph_id: int, timeout: float) -> str:
        """Wait until the graph is finished or paused; returns its state then (or now, after `timeout`)."""
        end = time.monotonic() + timeout
        while True:
            job = self.store.get_job(graph_id)
            state = job.state if job else CANCELLED
            if state in TERMINAL_STATES or state == PAUSED or time.monotonic() >= end:
                return state
            time.sleep(0.01)

    def idle(self, graph_id: int) -> bool:
        """No step of this graph is still running a try in this process."""
        with self._lock:
            return not any(t.is_alive() for t in self._live[graph_id])

    # -- inside ----------------------------------------------------------------------------------------------
    def _stale(self, job_id: int, attempt: int) -> bool:
        if self.killswitch is not None and getattr(self.killswitch, "is_set", False):
            return True
        job = self.store.get_job(job_id)
        return job is None or job.state != RUNNING or job.payload.get("attempt") != attempt

    def _drive(self, graph_id: int) -> None:
        """The graph's own job: starts ready steps until every step is finished, or the graph is paused/cancelled."""
        wake = self._wake[graph_id]
        while True:
            with self.store._lock:
                parent = self.store.get_job(graph_id)
                if parent is None or parent.state != RUNNING:
                    return                                       # paused or cancelled: the buttons did the rest
                steps = {j.payload.get("step"): j for j in self.store.list_jobs(parent_id=graph_id)}
                self._spread_failures(steps)
                if all(j.state in TERMINAL_STATES for j in steps.values()):
                    failed = [s for s, j in steps.items() if j.state == FAILED]
                    if failed:
                        raise GraphFailed(f"Step {', '.join(failed)} failed; the steps that needed it were cancelled.")
                    if any(j.state == CANCELLED for j in steps.values()):
                        self.store.set_state(graph_id, CANCELLED, error="A step was cancelled")
                    return
                waiting_for_slot = False
                for sid, j in steps.items():
                    if j.state != PENDING or not all(steps[n].state == DONE for n in j.payload.get("needs", [])):
                        continue
                    if not self.slots.try_acquire():
                        waiting_for_slot = True
                        if j.progress != "Waiting for a free slot":
                            self.store.update_job(j.id, progress="Waiting for a free slot")
                        continue
                    self._launch(graph_id, j, {n: steps[n].payload.get("result") for n in j.payload.get("needs", [])})
                note = f"Waiting: {self.slots.reason}" if waiting_for_slot and self.slots.reason else ""
                if parent.progress != note:
                    self.store.update_job(graph_id, progress=note)
            wake.wait(0.1)
            wake.clear()

    def _spread_failures(self, steps: dict[str, Job]) -> None:
        changed = True
        while changed:
            changed = False
            for j in steps.values():
                if j.state != PENDING:
                    continue
                needs = [steps[n] for n in j.payload.get("needs", []) if n in steps]
                why = NEED_FAILED if any(n.state == FAILED or n.error == NEED_FAILED for n in needs) else (
                    NEED_CANCELLED if any(n.state == CANCELLED for n in needs) else "")
                if why:
                    steps[j.payload["step"]] = self.store.set_state(j.id, CANCELLED, error=why)
                    changed = True

    def _launch(self, graph_id: int, job: Job, results: dict) -> None:
        sid = job.payload["step"]
        func = self._funcs.get((graph_id, sid)) or _KINDS.get(job.payload.get("step_kind", ""))
        attempt = int(job.payload.get("attempt") or 0) + 1
        self.store.merge_payload(job.id, attempt=attempt)
        self.store.set_state(job.id, RUNNING, progress="")
        ctx = StepContext(graph_id, job.id, sid, attempt, results, job.retries, self)
        base = self._contexts.get(graph_id) or contextvars.copy_context()
        thread = self.runner.run_existing(job.id, base.copy().run, args=(self._run_step, ctx, func),
                                          name=f"prometheus-graph-{graph_id}-{sid}", mark_running=False)
        with self._lock:
            self._live[graph_id] = {t for t in self._live[graph_id] if t.is_alive()}
            if thread is not None:
                self._live[graph_id].add(thread)

    def _run_step(self, ctx: StepContext, func: Callable | None) -> None:
        """One step's tries. Holds a slot while trying, gives it back while waiting to retry. Never raises."""
        has_slot = True
        try:
            while True:
                try:
                    if func is None:
                        raise RuntimeError("This step's work isn't known in this run of Prometheus.")
                    result = func(ctx, ctx_inputs(self.store, ctx.job_id))
                except Exception as e:                       # noqa: BLE001 - a step's own error is its result
                    with self.store._lock:
                        if ctx.should_stop():
                            return
                        job = self.store.get_job(ctx.job_id)
                        why = f"{type(e).__name__}: {e}"[:500]
                        if func is None or job.retries >= int(job.payload.get("max_retries", DEFAULT_RETRIES)):
                            self.store.set_state(ctx.job_id, FAILED, error=why)
                            return
                        tries = job.retries + 1
                        wait = self.waits[min(tries, len(self.waits)) - 1]
                        self.store.update_job(ctx.job_id, retries=tries, error=why,
                                              progress=f"Failed; trying again in {wait:g} s (retry {tries})")
                    ctx.retries = tries
                    self.slots.release()
                    has_slot = False
                    if ctx.wait(wait):
                        return
                    while not self.slots.try_acquire():          # back in line for a slot
                        if ctx.wait(0.1):
                            return
                    has_slot = True
                    continue
                with self.store._lock:
                    if ctx.should_stop():
                        return                                   # cancelled/paused meanwhile: the result is dropped
                    self.store.merge_payload(ctx.job_id, result=_jsonable(result))
                    self.store.set_state(ctx.job_id, DONE, progress="")
                return
        except Exception:
            log.exception("Graph step %s failed unexpectedly", ctx.step)
        finally:
            if has_slot:
                self.slots.release()
            self._wake[ctx.graph_id].set()


def ctx_inputs(store: JobStore, job_id: int) -> dict:
    job = store.get_job(job_id)
    return dict(job.payload.get("inputs") or {}) if job else {}


def _jsonable(value):
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"text": str(value)[:20000]}


def recover_graphs(store: JobStore) -> int:
    """After store.recover_after_restart(): a paused graph that can't carry on (in-memory steps) is closed."""
    closed = 0
    with store._lock:
        for parent in store.list_jobs(kind=GRAPH_KIND) + [j for j in store.list_jobs(state=PAUSED)
                                                          if j.payload.get("graph") and j.kind != GRAPH_KIND]:
            if parent.state in TERMINAL_STATES or parent.payload.get("resumable", True):
                continue
            for j in store.list_jobs(parent_id=parent.id):
                if j.state not in TERMINAL_STATES:
                    store.set_state(j.id, CANCELLED, error=NOT_RESUMABLE)
            store.set_state(parent.id, CANCELLED, error=NOT_RESUMABLE)
            closed += 1
    return closed
