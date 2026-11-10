"""The one interface every AI brain implements."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Reply:
    text: str
    provider: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    usd: float = 0.0


class ProviderUnavailable(Exception):
    """No usable model (not running, no key, refused, network error)."""


class PrivacyViolation(Exception):
    """A private (personal-data) route was configured with a cloud provider."""


class Provider(Protocol):
    name: str
    is_local: bool

    def available(self) -> bool: ...

    def complete(self, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096) -> Reply: ...
