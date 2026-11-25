"""Carrying on by itself after an AI limit.

Free AIs have hourly and daily allowances. When a request stops because every AI was busy, rate limited or out of
its allowance, the request is kept here and tried again later without the owner doing anything: after 6 minutes,
then 15, 30, and then every hour for a day. When it works, the answer simply appears in the same chat.

Not for requests that wait for a yes (a paid AI, an approval) or that hit the owner's own spending limit: those
need the owner. Stop all pauses the retries too.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid

from prometheus.jobs.states import CANCELLED, DONE, FAILED, PAUSED, PENDING, RUNNING
from prometheus.jobs.store import JobStore

log = logging.getLogger(__name__)

KV = "resume_queue"
WAITS = [6, 15, 30] + [60] * 24          # minutes between tries: about a day in all
CHECK_EVERY = 60
MAX_WAITING = 20
LIMITISH = re.compile(r"rate limit|out of (?:openrouter )?credit|resting|No AI model is available|empty answer|"
                      r"timed out|can't be reached|error 5\d\d|overloaded|quota|too many requests", re.I)


def worth_retrying(error: str) -> bool:
    return bool(LIMITISH.search(error or "")) and "use paid" not in (error or "")


# Every read-modify-write of the queue happens under this lock, so two updates can't lose each other's changes.
_LOCK = threading.RLock()
_IN_FLIGHT: set[str] = set()          # ids of waiting requests being started right now (never started twice)
_FINISHED: set[str] = set()           # ids already answered in this run of Prometheus (never started again)


def _get_job_store(store) -> JobStore | None:
    if hasattr(store, "jobs") and isinstance(getattr(store, "jobs"), JobStore):
        return store.jobs
    if hasattr(store, "_job_store_cached"):
        return store._job_store_cached
    try:
        js = JobStore(store)
        try:
            store._job_store_cached = js
        except Exception:
            pass
        return js
    except Exception:
        return None


def _valid(item) -> bool:
    return (isinstance(item, dict) and isinstance(item.get("chat_id"), int) and isinstance(item.get("text"), str)
            and isinstance(item.get("tries", 0), int))


def _queue(store) -> list[dict]:
    """The waiting requests. Malformed entries are dropped; old entries get an id."""
    with _LOCK:
        got = store.kv_get(KV, [])
        items = [q for q in got if _valid(q)] if isinstance(got, list) else []
        for q in items:
            q.setdefault("id", uuid.uuid4().hex)
            q.setdefault("tries", 0)
        return items


def _save(store, queue: list[dict]) -> None:
    with _LOCK:
        store.kv_set(KV, queue)


def _finish_jobs(store, item_id: str, final: str, why: str = "") -> None:
    """Close the tracker record of one waiting request (M5 review R3); never stops the queue itself."""
    job_store = _get_job_store(store)
    if job_store is None:
        return
    for j in job_store.list_jobs(kind="resume"):
        if j.payload.get("id") == item_id:
            try:
                job_store.finish(j.id, final, error=why or None)
            except Exception:
                log.exception("Couldn't close the tracker record of a waiting request")


def _without(store, item_id: str, status: str = "done") -> None:
    """Remove one waiting request by its id (never another request with the same text). `status` is how the task
    it became ended, so the tracker records the real result (M5 audit F7)."""
    final = {"done": DONE, "failed": FAILED}.get(status, CANCELLED)
    with _LOCK:
        _save(store, [q for q in _queue(store) if q.get("id") != item_id])
        _finish_jobs(store, item_id, final, "" if final == DONE else f"The request ended: {status}")


def note_limit(store, chat_id: int, text: str, error: str, now: float | None = None) -> str:
    """Called when a request stopped on an AI limit. Returns the sentence to add to the answer ('' = not kept)."""
    if not worth_retrying(error) or not text or len(text) > 4000:
        return ""
    now = time.time() if now is None else now
    with _LOCK:
        job_store = _get_job_store(store)
        existing_jobs = []
        if job_store is not None:
            existing_jobs = [j for j in job_store.list_jobs(kind="resume")
                             if j.payload.get("chat_id") == chat_id and j.payload.get("text") == text
                             and j.state not in (DONE, CANCELLED, FAILED)]
        item_id = existing_jobs[0].payload.get("id") if existing_jobs else uuid.uuid4().hex

        queue = [q for q in _queue(store) if q["id"] not in _FINISHED]
        item = next((q for q in queue if q["chat_id"] == chat_id and q["text"] == text), None)
        if item is None:
            if len(queue) >= MAX_WAITING:
                return ""
            item = {"id": item_id, "chat_id": chat_id, "text": text, "tries": 0, "first": now}
            queue.append(item)
            if not existing_jobs and job_store is not None:
                j = job_store.create_job(
                    kind="resume",
                    title=f"Retry: {text[:30]}",
                    payload=item,
                    state=PAUSED,
                )
                existing_jobs = [j]

        if item["tries"] >= len(WAITS):
            queue.remove(item)
            _save(store, queue)
            _finish_jobs(store, item["id"], FAILED, "Exceeded maximum retries (tried for a day)")
            return ("I've tried for a day and the AIs are still not answering, so I've stopped. "
                    "Ask me again when you like.")
        wait = WAITS[item["tries"]]
        item["tries"] += 1
        item["due"] = now + wait * 60
        _save(store, queue)
        if existing_jobs and job_store is not None:
            try:
                job_store.update_job(
                    existing_jobs[0].id,
                    retries=item["tries"],
                    error=error,
                    payload=item,
                )
            except Exception:
                pass
    when = f"{wait} minutes" if wait < 60 else "an hour"
    return f"I'll try this again by myself in {when}; the answer will appear here. (Say “stop trying” to cancel.)"


def drop(store, item_id: str, why: str = "Stopped by you") -> bool:
    """Stop one waiting request for good (the Stop button in the jobs view): out of the queue, never started again,
    and its tracker record cancelled. M5 review R9."""
    with _LOCK:
        queue = _queue(store)
        keep = [q for q in queue if q.get("id") != item_id]
        _FINISHED.add(item_id)
        _save(store, keep)
        _finish_jobs(store, item_id, CANCELLED, why)
        return len(keep) != len(queue)


def cancel(store, chat_id: int) -> int:
    with _LOCK:
        job_store = _get_job_store(store)
        if job_store is not None:
            for j in job_store.list_jobs(kind="resume"):
                if j.payload.get("chat_id") == chat_id and j.state not in (DONE, CANCELLED, FAILED):
                    try:
                        job_store.set_state(j.id, CANCELLED, error="Cancelled by user")
                    except Exception:
                        pass
        queue = _queue(store)
        keep = [q for q in queue if q["chat_id"] != chat_id]
        _save(store, keep)
        return len(queue) - len(keep)


def answer(store, chat_id: int, text: str) -> str | None:
    """Chat phrase: "stop trying"."""
    if re.match(r"^\W*(?:please\s+)?(?:stop|quit|cancel|don'?t) (?:re)?try(?:ing)?(?: (?:that|it|again))?\W*$", text or "", re.I):
        n = cancel(store, chat_id)
        return "OK, I've stopped trying." if n else "Nothing is waiting to be tried again in this chat."
    return None


class Resumer:
    def __init__(self, services, clock=time.time):
        self.services, self.clock = services, clock
        self._stop = threading.Event()

    def tick(self) -> int:
        """Start again any waiting request whose time has come. Returns how many were started."""
        s = self.services
        from prometheus.features import on, paused

        if paused(s.settings) or not on(s.settings, "resume"):
            return 0
        now = self.clock()
        to_start: list[dict] = []
        with _LOCK:
            queue = _queue(s.store)
            for item in queue:
                if item["id"] in _FINISHED:
                    continue                  # answered earlier; it is dropped from the queue below
                if item.get("due", 0) > now or item["chat_id"] in s.running or item["id"] in _IN_FLIGHT:
                    continue
                item["due"] = now + 3600      # while it runs it isn't started twice; a new limit sets the real time
                to_start.append(dict(item))
            leftovers = [q for q in queue if q["id"] in _FINISHED]
            if not to_start and not leftovers:
                return 0
            queue = [q for q in queue if q["id"] not in _FINISHED]
            _save(s.store, queue)             # if this fails nothing is marked running, so the next tick tries again
            _IN_FLIGHT.update(item["id"] for item in to_start)
            if not to_start:
                return 0
        started = 0
        for item in to_start:
            handed_over = False
            try:
                self._run(item)
                handed_over = True
                started += 1
            except Exception:                 # couldn't even start the worker thread
                log.exception("Couldn't start the retry worker")
                try:
                    self._failed(item, "the retry worker couldn't start")
                except Exception:
                    log.exception("Couldn't record a failed restart")
            finally:
                if not handed_over:
                    with _LOCK:
                        _IN_FLIGHT.discard(item["id"])
        return started

    def _notify(self, text: str) -> None:
        s = self.services
        if hasattr(s, "notify"):
            try:
                s.notify("task", text)
            except Exception:                 # a broken notifier never stops the retries
                log.warning("Couldn't show a notification about a waiting request", exc_info=True)

    def _failed(self, item: dict, why: str) -> None:
        """A restart crashed: count it as a try, wait for the next turn, and give up after the last one."""
        s = self.services
        gave_up = False
        try:
            with _LOCK:
                queue = _queue(s.store)
                q = next((x for x in queue if x["id"] == item["id"]), None)
                if q is not None:
                    q["tries"] = int(q.get("tries", 0)) + 1
                    q["last_error"] = why[:200]
                    if q["tries"] >= len(WAITS):      # the same limit as note_limit
                        queue.remove(q)
                        gave_up = True
                        _finish_jobs(s.store, item["id"], FAILED, why[:200])
                    else:
                        q["due"] = self.clock() + WAITS[min(q["tries"], len(WAITS)) - 1] * 60
                    _save(s.store, queue)
        finally:
            with _LOCK:
                _IN_FLIGHT.discard(item["id"])
        if gave_up:
            self._notify("A request that was waiting for the AI couldn't be restarted, so I've stopped trying. "
                         "Please ask again.")

    def _run(self, item: dict) -> None:
        s = self.services

        def work():
            try:
                thread = s.start_task(item["chat_id"], item["text"])
            except Exception as err:
                log.exception("Restarting a waiting request failed")
                try:
                    self._failed(item, f"restart failed ({type(err).__name__})")   # releases the in-flight mark
                except Exception:
                    log.exception("Couldn't record a failed restart")
                    with _LOCK:
                        _IN_FLIGHT.discard(item["id"])
                return
            # From here on the request has run (or is running): an error below never makes it run again.
            try:
                end = time.monotonic() + 1800
                time.sleep(0.05)
                # the answer is in as soon as the chat stops running (notifications etc. can take longer)
                while (thread is not None and thread.is_alive() and item["chat_id"] in s.running
                       and time.monotonic() < end):
                    time.sleep(0.05)
                task = s.store.latest_task(item["chat_id"])
                if task and task["status"] != "paused":       # answered (or failed for another reason): done waiting
                    with _LOCK:
                        _FINISHED.add(item["id"])            # never started again, even if removing it fails
                    for attempt in range(3):
                        try:
                            _without(s.store, item["id"], status=str(task["status"]))
                            break
                        except Exception:
                            if attempt == 2:
                                raise
                            time.sleep(0.2)
                    if task["status"] == "done":
                        self._notify("The AI is answering again: your waiting request is done.")
            except Exception:
                log.exception("Tidying up after a resumed request failed (it is not run again)")
            finally:
                with _LOCK:
                    _IN_FLIGHT.discard(item["id"])

        from prometheus.runtime import start_job

        start_job("resume_task", f"Resuming waiting request {item.get('id')}", work, name="prometheus-resume")

    def loop(self) -> None:
        while not self._stop.wait(CHECK_EVERY):
            try:
                self.tick()
            except Exception:                 # keep the loop alive, but leave a trace
                log.exception("Checking waiting requests failed")

    def start(self) -> None:
        from prometheus.runtime import start_job

        start_job("resumer", "Resumer loop", self.loop, name="prometheus-resumer", service=True)

    def stop(self) -> None:
        self._stop.set()
