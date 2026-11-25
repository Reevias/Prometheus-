"""Child agents at once (M5 step 5.3), sized for the owner's 8 GB laptop.

- RamSlots: at most 2 child agents at once, 3 when more than 3 GB of RAM is free, none new under 1 GB free. Free RAM is
  read with psutil before every start; a reading that fails is never guessed (the safe 2 is used, shown as unknown).
- Programs a child agent starts (through the M1 terminal) go in a Windows Job Object with kill-on-close and a memory
  cap (CHILD_MEMORY_CAP); on Linux in their own process group. They are written down here and in the job's record.
- Clean-up: when a child ends, is cancelled or paused, or Stop all is pressed, its programs are ended. At start-up,
  programs written down by a run that crashed are ended too, but only if the pid still belongs to the same program
  (its start time matches), so a reused pid number is never touched.
- child_status: state, how long it has run, and the real memory of the programs it started (None when it started none:
  a child's own Python work shares Prometheus's memory and can't be measured apart, so nothing is made up).
"""
from __future__ import annotations

import contextvars
import itertools
import logging
import os
import signal
import sys
import threading
import time
from typing import Callable

from prometheus.jobs.graph import STEP_KIND, Slots

log = logging.getLogger(__name__)

GB = 1024 ** 3
LOW_MEMORY = 1 * GB                     # under this much free RAM nothing new starts
ROOMY_MEMORY = 3 * GB                   # over this much free RAM a third child may run
CHILD_MEMORY_CAP = 1536 * 1024 * 1024   # the most RAM one child's programs may use together (Windows Job Object)
CHILD_KINDS = {STEP_KIND}               # jobs that count as child agents (own agents join in 5.4)


def _psutil_free() -> float | None:
    try:
        import psutil

        return float(psutil.virtual_memory().available)
    except Exception:                    # noqa: BLE001 - no reading is better than a made-up one
        return None


class RamSlots(Slots):
    """The 5.3 limit: 2 at once, 3 with more than 3 GB free, none new under 1 GB free."""

    def __init__(self, free_bytes: Callable[[], float | None] | None = None):
        super().__init__(2)
        self._free = free_bytes or _psutil_free
        self.free_gb: float | None = None

    def try_acquire(self) -> bool:
        free = self._free()
        with self._lock:
            self.free_gb = None if free is None else round(free / GB, 1)
            if free is not None and free < LOW_MEMORY:
                # the 8 GB laptop often sits under 1 GB free with normal apps open: one at a time then, never none
                # (laptop check, 25 Nov: "none" made every graph wait for ever)
                self.size = 1
                if self._used >= 1:
                    self.reason = (f"only one at a time while memory is low ({free / GB:.1f} GB free; more start when "
                                   f"{LOW_MEMORY // GB} GB is free)")
                    return False
            else:
                self.size = 3 if free is not None and free > ROOMY_MEMORY else 2
                if self._used >= self.size:
                    self.reason = "waiting for a free slot"
                    return False
            self._used += 1
            self.reason = ""
            return True


_SLOTS: RamSlots | None = None
_SLOTS_LOCK = threading.Lock()


def child_slots() -> RamSlots:
    """The one limit shared by every child agent in this run of Prometheus."""
    global _SLOTS
    with _SLOTS_LOCK:
        if _SLOTS is None:
            _SLOTS = RamSlots()
        return _SLOTS


_TASK_SLOTS: RamSlots | None = None


def task_slots() -> RamSlots:
    """A separate limit, of the same size, for chat tasks (laptop check: 5 chats at once all ran). Separate so a task
    holding its slot never waits for a slot its own graph steps need."""
    global _TASK_SLOTS
    with _SLOTS_LOCK:
        if _TASK_SLOTS is None:
            _TASK_SLOTS = RamSlots()
        return _TASK_SLOTS


def wait_for_slot(slots: Slots, give_up: Callable[[], bool], waiting: Callable[[], None] | None = None) -> bool:
    """Block until a slot is free (True) or give_up() says stop (False). `waiting` runs once if it has to wait."""
    told = False
    while not slots.try_acquire():
        if give_up():
            return False
        if not told and waiting is not None:
            waiting()
            told = True
        time.sleep(0.1)
    return True


# -- programs started by child agents --------------------------------------------------------------------------
CURRENT_ATTEMPT: contextvars.ContextVar[int] = contextvars.ContextVar("prometheus_step_attempt", default=0)
_PROGRAMS: dict[int, dict] = {}
_PROGRAMS_LOCK = threading.RLock()
_TOKENS = itertools.count(1)


def _current_child():
    """(job id, store) when this code runs inside a child agent's job, else (None, None)."""
    from prometheus.jobs.runner import _CURRENT_JOB_RUNNER, current_job_id

    jid, runner = current_job_id(), _CURRENT_JOB_RUNNER.get()
    if jid is None or runner is None or getattr(runner, "store", None) is None:
        return None, None
    try:
        job = runner.store.get_job(jid)
    except Exception:                    # noqa: BLE001
        return None, None
    return (jid, runner.store) if job is not None and job.kind in CHILD_KINDS else (None, None)


def cap_for_current() -> int | None:
    """The Job Object memory cap for a program started now: only inside a child agent."""
    jid, _ = _current_child()
    return CHILD_MEMORY_CAP if jid is not None else None


def _created(pid: int) -> float | None:
    try:
        import psutil

        return psutil.Process(pid).create_time()
    except Exception:                    # noqa: BLE001
        return None


def track(pid: int, pgid: int | None = None, win_job=None) -> int | None:
    """Write down a program a child agent started (the terminal calls this). Returns a token for untrack()."""
    jid, store = _current_child()
    if jid is None:
        return None
    token = next(_TOKENS)
    entry = {"job_id": jid, "pid": pid, "pgid": pgid, "win_job": win_job, "created": _created(pid), "store": store,
             "attempt": CURRENT_ATTEMPT.get()}
    with _PROGRAMS_LOCK:
        _PROGRAMS[token] = entry
        _save(store, jid)
    if _KILLSWITCH is not None and getattr(_KILLSWITCH, "is_set", False):
        _end(entry)                          # started while everything is stopped: it doesn't get to run
    return token


def untrack(token: int | None) -> None:
    if token is None:
        return
    with _PROGRAMS_LOCK:
        entry = _PROGRAMS.pop(token, None)
        if entry is not None:
            _save(entry["store"], entry["job_id"])


def _save(store, jid: int) -> None:
    """The job's record lists its live programs, so a crash can be cleaned up at the next start."""
    try:
        progs = [{"pid": e["pid"], "created": e["created"]} for e in _PROGRAMS.values() if e["job_id"] == jid]
        store.merge_payload(jid, programs=progs)
    except Exception:                    # noqa: BLE001 - a full disk must not stop the program from being ended
        log.exception("Couldn't write down a child's programs")


def _end(entry: dict) -> None:
    from prometheus.actions.terminal import kill_process_tree

    pid, pgid, job = entry["pid"], entry.get("pgid"), entry.get("win_job")
    if sys.platform == "win32":
        if job is not None:
            job.close()                  # kill-on-close ends the whole tree at once, with no pid lookup at all
        else:
            kill_process_tree(pid)       # the caller still holds the process handle, so this pid can't be reused yet
        return
    if pgid is not None and pgid != os.getpgrp():
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError):
            pass
    kill_process_tree(pid)


def end_job(job_id: int, attempt: int | None = None) -> int:
    """End every program this child started (only those of one try, when `attempt` is given: a finished try never
    ends the next try's programs; M5 audit F5). Returns how many were ended."""
    with _PROGRAMS_LOCK:
        entries = [e for e in _PROGRAMS.values()
                   if e["job_id"] == job_id and (attempt is None or e.get("attempt") == attempt)]
    for e in entries:
        _end(e)
    return len(entries)


def end_all(reason: str = "") -> int:
    """Stop all: end every program of every child agent."""
    with _PROGRAMS_LOCK:
        entries = list(_PROGRAMS.values())
    for e in entries:
        _end(e)
    return len(entries)


def running_programs() -> dict[int, dict]:
    with _PROGRAMS_LOCK:
        return {k: {kk: vv for kk, vv in v.items() if kk not in ("win_job", "store")} for k, v in _PROGRAMS.items()}


_KILLSWITCH = None


def connect_killswitch(killswitch) -> None:
    global _KILLSWITCH
    _KILLSWITCH = killswitch
    killswitch.on_stop(end_all)


def connect_stop_all(killswitch, job_store) -> None:
    """Stop all, in this order (laptop check, 25 Nov): first every running job is paused, so a step that ends
    because its program was killed is never recorded as done and its graph starts nothing new; then every child's
    programs are ended. A program a child starts while everything is stopped is ended at once (track)."""
    killswitch.on_stop(lambda reason: job_store.pause_all(f"Stopped by user: {reason}" if reason else "Stopped by user"))
    connect_killswitch(killswitch)


def end_leftovers(store) -> int:
    """At start-up: end programs written down by a run that crashed, only if each pid is still the same program."""
    import psutil

    ended = 0
    for job in store.list_with_programs():
        progs = job.payload.get("programs") if isinstance(job.payload, dict) else None
        if not progs:
            continue
        for p in progs:
            try:
                proc = psutil.Process(int(p["pid"]))
                if p.get("created") is None or abs(proc.create_time() - float(p["created"])) > 0.5:
                    continue                                   # the pid number now belongs to another program
                for child in proc.children(recursive=True):
                    child.kill()
                proc.kill()
                ended += 1
            except (psutil.Error, KeyError, ValueError, TypeError):
                continue
        store.merge_payload(job.id, programs=[])
    return ended


def child_status(store, job_id: int) -> dict:
    """State, seconds it has run, and the real memory (RSS) of its programs (None when it started none)."""
    job = store.get_job(job_id)
    if job is None:
        return {"state": None, "seconds": 0.0, "memory_bytes": None}
    end = job.finished or time.time()
    seconds = max(0.0, end - job.started) if job.started else 0.0
    with _PROGRAMS_LOCK:
        pids = [e["pid"] for e in _PROGRAMS.values() if e["job_id"] == job_id]
    memory = None
    if pids:
        import psutil

        total, seen = 0, False
        for pid in pids:
            try:
                proc = psutil.Process(pid)
                for p in [proc, *proc.children(recursive=True)]:
                    try:
                        total += p.memory_info().rss
                        seen = True
                    except psutil.Error:
                        pass
            except psutil.Error:
                pass
        memory = total if seen else None
    return {"state": job.state, "seconds": seconds, "memory_bytes": memory}
