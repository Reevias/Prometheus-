"""Carrying on by itself after an AI limit.

Free AIs have hourly and daily allowances. When a request stops because every AI was busy, rate limited or out of
its allowance, the request is kept here and tried again later without the owner doing anything: after 6 minutes,
then 15, 30, and then every hour for a day. When it works, the answer simply appears in the same chat.

Not for requests that wait for a yes (a paid AI, an approval) or that hit the owner's own spending limit: those
need the owner. Stop all pauses the retries too.
"""
from __future__ import annotations

import re
import threading
import time

KV = "resume_queue"
WAITS = [6, 15, 30] + [60] * 24          # minutes between tries: about a day in all
CHECK_EVERY = 60
MAX_WAITING = 20
LIMITISH = re.compile(r"rate limit|out of (?:openrouter )?credit|resting|No AI model is available|empty answer|"
                      r"timed out|can't be reached|error 5\d\d|overloaded|quota|too many requests", re.I)


def worth_retrying(error: str) -> bool:
    return bool(LIMITISH.search(error or "")) and "use paid" not in (error or "")


def _queue(store) -> list[dict]:
    got = store.kv_get(KV, [])
    return got if isinstance(got, list) else []


def note_limit(store, chat_id: int, text: str, error: str, now: float | None = None) -> str:
    """Called when a request stopped on an AI limit. Returns the sentence to add to the answer ('' = not kept)."""
    if not worth_retrying(error) or not text or len(text) > 4000:
        return ""
    now = time.time() if now is None else now
    queue = _queue(store)
    item = next((q for q in queue if q["chat_id"] == chat_id and q["text"] == text), None)
    if item is None:
        if len(queue) >= MAX_WAITING:
            return ""
        item = {"chat_id": chat_id, "text": text, "tries": 0, "first": now}
        queue.append(item)
    if item["tries"] >= len(WAITS):
        queue.remove(item)
        store.kv_set(KV, queue)
        return "I've tried for a day and the AIs are still not answering, so I've stopped. Ask me again when you like."
    wait = WAITS[item["tries"]]
    item["tries"] += 1
    item["due"] = now + wait * 60
    store.kv_set(KV, queue)
    when = f"{wait} minutes" if wait < 60 else "an hour"
    return f"I'll try this again by myself in {when}; the answer will appear here. (Say “stop trying” to cancel.)"


def cancel(store, chat_id: int) -> int:
    queue = _queue(store)
    keep = [q for q in queue if q["chat_id"] != chat_id]
    store.kv_set(KV, keep)
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
        now, started = self.clock(), 0
        for item in list(_queue(s.store)):
            if item.get("due", 0) > now or item["chat_id"] in s.running:
                continue
            item["due"] = now + 3600          # while it runs it isn't started twice; a new limit sets the real time
            queue = _queue(s.store)
            for q in queue:
                if q["chat_id"] == item["chat_id"] and q["text"] == item["text"]:
                    q["due"] = item["due"]
            s.store.kv_set(KV, queue)
            self._run(item)
            started += 1
        return started

    def _run(self, item: dict) -> None:
        s = self.services

        def work():
            thread = s.start_task(item["chat_id"], item["text"])
            end = time.monotonic() + 1800
            time.sleep(0.05)
            # the answer is in as soon as the chat stops running (what follows it, like notifications, can take longer)
            while thread.is_alive() and item["chat_id"] in s.running and time.monotonic() < end:
                time.sleep(0.05)
            task = s.store.latest_task(item["chat_id"])
            if task and task["status"] != "paused":           # answered (or failed for another reason): done waiting
                queue = [q for q in _queue(s.store)
                         if not (q["chat_id"] == item["chat_id"] and q["text"] == item["text"])]
                s.store.kv_set(KV, queue)
                if task["status"] == "done" and hasattr(s, "notify"):
                    try:
                        s.notify("task", "The AI is answering again: your waiting request is done.")
                    except Exception:
                        pass

        threading.Thread(target=work, name="prometheus-resume", daemon=True).start()

    def loop(self) -> None:
        while not self._stop.wait(CHECK_EVERY):
            try:
                self.tick()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-resumer", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
