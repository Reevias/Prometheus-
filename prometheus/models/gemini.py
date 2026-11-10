"""Gemini 4 Argon (Google). Stub until the user adds a Gemini API key (v1.1)."""
from __future__ import annotations

from prometheus.models.base import ProviderUnavailable, Reply


class GeminiProvider:
    is_local = False

    def __init__(self, name: str = "gemini"):
        self.name = name

    def available(self) -> bool:
        return False

    def complete(self, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096) -> Reply:
        raise ProviderUnavailable("Gemini (Argon) is not connected yet.")
