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


def _without(store, item_id: str) -> None:
    """Remove one waiting request by its id (never another request with the same text)."""
    with _LOCK:
        _save(store, [q for q in _queue(store) if q.get("id") != item_id])


def note_limit(store, chat_id: int, text: str, error: str, now: float | None = None) -> str:
    """Called when a request stopped on an AI limit. Returns the sentence to add to the answer ('' = not kept)."""
    if not worth_retrying(error) or not text or len(text) > 4000:
        return ""
    now = time.time() if now is None else now
    with _LOCK:
        queue = _queue(store)
        item = next((q for q in queue if q["chat_id"] == chat_id and q["text"] == text), None)
        if item is None:
            if len(queue) >= MAX_WAITING:
                return ""
            item = {"id": uuid.uuid4().hex, "chat_id": chat_id, "text": text, "tries": 0, "first": now}
            queue.append(item)
        if item["tries"] >= len(WAITS):
            queue.remove(item)
            _save(store, queue)
            return ("I've tried for a day and the AIs are still not answering, so I've stopped. "
                    "Ask me again when you like.")
        wait = WAITS[item["tries"]]
        item["tries"] += 1
        item["due"] = now + wait * 60
        _save(store, queue)
    when = f"{wait} minutes" if wait < 60 else "an hour"
    return f"I'll try this again by myself in {when}; the answer will appear here. (Say “stop trying” to cancel.)"


def cancel(store, chat_id: int) -> int:
    with _LOCK:
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
                if item.get("due", 0) > now or item["chat_id"] in s.running or item["id"] in _IN_FLIGHT:
                    continue
                item["due"] = now + 3600      # while it runs it isn't started twice; a new limit sets the real time
                _IN_FLIGHT.add(item["id"])
                to_start.append(dict(item))
            if to_start:
                _save(s.store, queue)
        for item in to_start:
            try:
                self._run(item)
            except Exception:                 # couldn't even start the worker thread
                log.exception("Couldn't start the retry worker")
                self._failed(item, "the retry worker couldn't start")
        return len(to_start)

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
                    if q["tries"] > len(WAITS):
                        queue.remove(q)
                        gave_up = True
                    else:
                        q["due"] = self.clock() + WAITS[min(q["tries"], len(WAITS)) - 1] * 60
                    _save(s.store, queue)
        finally:
            _IN_FLIGHT.discard(item["id"])
        if gave_up:
            self._notify("A request that was waiting for the AI couldn't be restarted, so I've stopped trying. "
                         "Please ask again.")

    def _run(self, item: dict) -> None:
        s = self.services

        def work():
            try:
                try:
                    thread = s.start_task(item["chat_id"], item["text"])
                except Exception as err:
                    log.exception("Restarting a waiting request failed")
                    self._failed(item, f"restart failed ({type(err).__name__})")
                    return
                end = time.monotonic() + 1800
                time.sleep(0.05)
                # the answer is in as soon as the chat stops running (notifications etc. can take longer)
                while (thread is not None and thread.is_alive() and item["chat_id"] in s.running
                       and time.monotonic() < end):
                    time.sleep(0.05)
                task = s.store.latest_task(item["chat_id"])
                if task and task["status"] != "paused":       # answered (or failed for another reason): done waiting
                    _without(s.store, item["id"])
                    if task["status"] == "done":
                        self._notify("The AI is answering again: your waiting request is done.")
            except Exception as err:
                log.exception("The retry worker failed")
                self._failed(item, f"retry worker failed ({type(err).__name__})")
                return
            finally:
                _IN_FLIGHT.discard(item["id"])

        threading.Thread(target=work, name="prometheus-resume", daemon=True).start()

    def loop(self) -> None:
        while not self._stop.wait(CHECK_EVERY):
            try:
                self.tick()
            except Exception:                 # keep the loop alive, but leave a trace
                log.exception("Checking waiting requests failed")

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-resumer", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
