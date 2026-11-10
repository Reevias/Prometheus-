"""One switch that stops every agent and model call."""
from __future__ import annotations

import threading


class Stopped(Exception):
    pass


class KillSwitch:
    def __init__(self):
        self._event = threading.Event()
        self.reason = ""

    def trigger(self, reason: str) -> None:
        self.reason = reason
        self._event.set()

    def reset(self) -> None:
        self.reason = ""
        self._event.clear()

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Stopped(f"Stopped: {self.reason}")
