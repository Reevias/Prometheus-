"""Phone jobs that wait for the phone (M3.2, owner: "queue it and tell me").

When a request needs the phone (phone control) and the phone isn't reachable, the request is kept here. Every two
minutes Prometheus checks whether the phone is back; when it is, the request runs again in its chat and the owner gets
a notice. A request waits at most a day, then the owner is told it was dropped.
"""
from __future__ import annotations

import threading
import time
import uuid

KV = "phone_waiting"
TRIED = "phone_waiting_tried"            # {chat:text: [first time, tries]}: so a job that keeps failing ends
MAX_TRIES = 3
_LOCK = threading.Lock()
WAIT_TEXT = "Your phone isn't reachable right now, so this waits"
CHECK_EVERY = 120
MAX_WAIT = 24 * 3600
MAX_JOBS = 20
OFFLINE = ("isn't connected", "didn't answer in time", "device offline")


def is_offline(error: str) -> bool:
    return any(w in str(error) for w in OFFLINE)


def jobs(store) -> list[dict]:
    got = store.kv_get(KV, [])
    return [j for j in got if isinstance(j, dict) and j.get("text") and j.get("chat_id")] if isinstance(got, list) else []


def _key(chat_id: int, text: str) -> str:
    return f"{chat_id}:{text[:4000]}"


def queue(store, chat_id: int, text: str, now: float | None = None) -> bool:
    """Keep a request for when the phone is back. False if it was already waiting, the list is full, or it was
    already tried 3 times (a phone that answers but keeps failing must not repeat the request for ever)."""
    now = time.time() if now is None else now
    with _LOCK:
        waiting = jobs(store)
        if any(j["chat_id"] == chat_id and j["text"] == text for j in waiting) or len(waiting) >= MAX_JOBS:
            return False
        tried = store.kv_get(TRIED, {}) or {}
        first, tries = (tried.get(_key(chat_id, text)) or [now, 0])[:2]
        if tries >= MAX_TRIES or now - float(first) >= MAX_WAIT:
            return False
        waiting.append({"id": uuid.uuid4().hex, "chat_id": chat_id, "text": text[:4000], "at": float(first)})
        store.kv_set(KV, waiting)
        return True


def phone_back(phone) -> bool:
    """Is the phone reachable? Reconnects once if it was paired before (wireless debugging)."""
    from prometheus.connectors.android import PhoneError

    if not getattr(phone, "target", ""):
        return False
    try:
        phone.shell("echo", "ok", timeout=10)
        return True
    except PhoneError:
        try:
            phone.connect(phone.target)
            return True
        except PhoneError:
            return False


class PhoneWaiter:
    def __init__(self, services, make_phone=None):
        self.services = services
        self.make_phone = make_phone
        self._stop = threading.Event()

    def _phone(self):
        if self.make_phone is not None:
            return self.make_phone()
        from prometheus.agents.phone_agent import phone
        return phone()

    def once(self, now: float | None = None) -> int:
        """Run the waiting requests if the phone is back. Returns how many were started."""
        store = self.services.store
        now = time.time() if now is None else now
        with _LOCK:
            waiting = jobs(store)
        if not waiting:
            return 0
        fresh = [j for j in waiting if now - float(j.get("at", now)) < MAX_WAIT]
        for j in waiting:
            if j not in fresh:
                store.add_message(j["chat_id"], "assistant", f"I waited a day for your phone, so I dropped this: "
                                                             f"“{j['text'][:200]}”. Ask again when the phone is on.",
                                  "phone")
        if len(fresh) != len(waiting):
            with _LOCK:
                store.kv_set(KV, [j for j in jobs(store) if j in fresh or j not in waiting])
        if not fresh or not phone_back(self._phone()):
            return 0
        chats = dict(store.chats())
        go = [j for j in fresh if j["chat_id"] not in self.services.running and j["chat_id"] in chats]
        with _LOCK:                                   # busy chats keep their job for the next check
            store.kv_set(KV, [j for j in jobs(store) if j not in go])
            tried = store.kv_get(TRIED, {}) or {}
            for j in go:
                first, tries = (tried.get(_key(j["chat_id"], j["text"])) or [j.get("at", now), 0])[:2]
                tried[_key(j["chat_id"], j["text"])] = [first, tries + 1]
            store.kv_set(TRIED, {k: v for k, v in tried.items() if now - float(v[0]) < MAX_WAIT})
        started = 0
        for j in go:
            store.add_message(j["chat_id"], "assistant", "Your phone is back, so I'm doing this now.", "phone")
            self.services.start_task(j["chat_id"], j["text"])
            started += 1
        if started:
            try:
                self.services.notify("phone", f"Your phone is back: doing {started} waiting request"
                                              f"{'s' if started != 1 else ''}.")
            except Exception:
                pass
        return started

    def loop(self) -> None:
        from prometheus.features import running

        while not self._stop.wait(CHECK_EVERY):
            if not running(self.services.settings, "phone"):
                continue
            try:
                self.once()
            except Exception:
                pass

    def start(self) -> None:
        from prometheus.jobs.runner import start_job

        start_job("phone_wait", "Phone waiter loop", self.loop, name="prometheus-phone-waiter", service=True)

    def stop(self) -> None:
        self._stop.set()
