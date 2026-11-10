"""Spending meter: warn at 80 %, stop at 100 % of the cap."""
from __future__ import annotations

from dataclasses import dataclass

from prometheus.memory.store import Store

WARN_AT = 0.8


class BudgetExceeded(Exception):
    pass


@dataclass
class BudgetState:
    spent: float
    cap: float
    warn: bool
    stopped: bool


class Budget:
    def __init__(self, store: Store, cap_usd: float):
        self.store = store
        self.cap_usd = cap_usd

    def record(self, provider: str, model: str, usd: float, tokens_in: int, tokens_out: int) -> None:
        self.store.add_cost(provider, model, usd, tokens_in, tokens_out)

    def state(self) -> BudgetState:
        spent = round(self.store.total_cost(), 6)
        return BudgetState(spent, self.cap_usd, spent >= WARN_AT * self.cap_usd, spent >= self.cap_usd)

    def ensure_can_spend(self) -> None:
        s = self.state()
        if s.stopped:
            raise BudgetExceeded(f"Spending cap reached (${s.spent:.2f} of ${s.cap:.2f}). Raise it in Settings.")
