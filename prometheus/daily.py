"""The end-of-day jobs: repo of the day, then updating Prometheus. Runs once a day at DAILY_AT (laptop time).

If the laptop was off at that time, the jobs run a few minutes after the next start instead. Updating needs no
approval; it only fast-forwards and waits while a task is running.
"""
from __future__ import annotations

import threading
import time

DAILY_AT = (23, 30)          # hour, minute: the end of the day
CHECK_EVERY = 300            # seconds between looks at the clock
CATCH_UP_AFTER = 30 * 3600   # missed for this long (laptop was off): run soon after start
START_DELAY = 300


def due(now: float, last: float, at=DAILY_AT) -> bool:
    """True when today's end-of-day time has passed and the jobs haven't run since then, or a day was missed."""
    lt = time.localtime(now)
    today_at = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, at[0], at[1], 0, 0, 0, -1))
    return (now >= today_at and last < today_at) or (now - last >= CATCH_UP_AFTER)


class DailyJobs:
    def __init__(self, services, clock=time.time):
        self.services = services
        self.clock = clock
        self._stop = threading.Event()
        self.log: list[str] = []

    def run_once(self, background: bool = False) -> list[str]:
        store, s = self.services.store, self.services.settings
        store.kv_set("daily_last", self.clock())
        done = []
        try:
            from prometheus.learning import learn

            lesson = learn(self.services) if getattr(self.services, "prompts", None) is not None else ""
            done.append(f"Learned: {lesson}" if lesson else "Nothing new to learn.")
        except Exception as e:               # no model available tonight: the ratings wait for tomorrow
            done.append(f"Learning skipped: {type(e).__name__}")
        orchestrator = getattr(self.services, "orchestrator", None)
        if orchestrator is not None and getattr(orchestrator, "facts", None) is not None:
            try:
                tidied = orchestrator.facts.tidy()
                done.append(f"Memory tidy-up: {len(tidied)} changes." if tidied else "Memory is tidy.")
            except Exception as e:
                done.append(f"Memory tidy-up skipped: {type(e).__name__}")
        if getattr(s, "repo_of_day", True):
            try:
                from prometheus.repo_of_day import RepoOfTheDay

                done.append(RepoOfTheDay(self.services).suggest())
            except Exception as e:           # offline or GitHub changed its page: try again tomorrow
                done.append(f"Repo of the day failed: {type(e).__name__}")
        updater = self.services.updater
        if updater is not None:
            msg = updater.check_once()
            done.append(msg or "No update needed.")
            if msg and hasattr(self.services, "notify"):
                self.services.notify("update", f"Prometheus updated: {msg[:120]}")
            if msg and background:
                updater.restart_when_idle()
        self.log = done
        store.audit("system", "daily", {"done": done})
        return done

    def loop(self, background: bool = False) -> None:
        if self._stop.wait(START_DELAY):
            return
        while True:
            if due(self.clock(), float(self.services.store.kv_get("daily_last", 0) or 0)):
                self.run_once(background)
            if self._stop.wait(CHECK_EVERY):
                return

    def start(self, background: bool = False) -> None:
        threading.Thread(target=self.loop, args=(background,), name="prometheus-daily", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
