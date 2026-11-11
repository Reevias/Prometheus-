"""Spending meter for paid AI, per calendar month: warn at 80 %, stop at 100 % of the monthly cap."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from prometheus.memory.store import Store

WARN_AT = 0.8
CURRENT = threading.local()       # .chat_id: the chat whose task is running on this thread (for the cost page)


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetState:
    spent: float
    cap: float
    warn: bool
    stopped: bool


def month_start(now: float | None = None) -> float:
    lt = time.localtime(time.time() if now is None else now)
    return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


def for_chat(chat_id: int) -> None:
    CURRENT.chat_id = int(chat_id or 0)


class Budget:
    def __init__(self, store: Store, cap_usd: float, clock=time.time):
        self.store = store
        self.cap_usd = cap_usd
        self.clock = clock
        self.on_warn = None           # called once a month with a message when 80 % is passed

    def record(self, provider: str, model: str, usd: float, tokens_in: int, tokens_out: int) -> None:
        before = self.state()
        self.store.add_cost(provider, model, usd, tokens_in, tokens_out, getattr(CURRENT, "chat_id", 0))
        after = self.state()
        month = time.strftime("%Y-%m", time.localtime(self.clock()))
        if after.warn and not before.stopped and self.store.kv_get("budget_warned") != month:
            self.store.kv_set("budget_warned", month)
            if self.on_warn is not None:
                try:
                    self.on_warn(f"Paid AI this month: ${after.spent:.2f} of your ${after.cap:.2f} limit (80% used). "
                                 "It stops at 100%; free and local models keep working.")
                except Exception:
                    pass

    def state(self) -> BudgetState:
        spent = round(self.store.total_cost(since=month_start(self.clock())), 6)
        return BudgetState(spent, self.cap_usd, spent >= WARN_AT * self.cap_usd, spent >= self.cap_usd)

    def ensure_can_spend(self) -> None:
        s = self.state()
        if s.stopped:
            raise BudgetExceeded(f"This month's limit for paid AI is reached (${s.spent:.2f} of ${s.cap:.2f}). "
                                 "Raise it in Settings, or wait for next month.")
