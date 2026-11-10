"""Scripted provider for tests and for running with no models at all."""
from __future__ import annotations

from typing import Callable

from prometheus.models.base import Reply


class FakeProvider:
    def __init__(self, replies: list[str] | Callable[[list[dict], str], str], name: str = "fake",
                 is_local: bool = True, usd: float = 0.0):
        self.name = name
        self.is_local = is_local
        self.usd = usd
        self._replies = replies
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def complete(self, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096, model: str | None = None) -> Reply:
        self.calls.append({"messages": messages, "system": system, "json_schema": json_schema, "model": model})
        if callable(self._replies):
            text = self._replies(messages, system)
        else:
            text = self._replies[min(len(self.calls), len(self._replies)) - 1]
        return Reply(text=text, provider=self.name, model=model or "fake", usd=self.usd)
