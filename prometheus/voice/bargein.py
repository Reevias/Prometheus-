"""Talking over Prometheus: while it speaks, the microphone keeps listening, and the owner's voice stops it.

The hard part is that, on laptop speakers, the microphone also hears Prometheus itself. So for the first
moments of each answer it learns how loud its own voice is in the microphone (the echo). The owner's voice
counts only when it is clearly louder than that echo for a quarter of a second. On headphones the echo is
almost nothing, so any normal speech cuts in: it works out which one you're using by itself.
"""
from __future__ import annotations

import threading
from collections import deque

from prometheus.voice.audio import FRAME, RATE, rms

FRAME_S = FRAME / RATE
LEARN = 0.45            # seconds at the start spent learning the echo
NEED = 8                # loud frames in a row (240 ms) that mean someone is talking
OVER_ECHO = 2.2         # how much louder than the echo the owner must be
KEEP = 2.0              # seconds of what was said kept for the next turn


class BargeIn:
    def __init__(self, mic, noise: float, stop: threading.Event, on_cut=lambda: None):
        self.mic = mic
        self.noise = max(noise, 100.0)
        self.stop = stop
        self.on_cut = on_cut
        self.done = threading.Event()
        self.cut = False
        self._levels: deque[float] = deque(maxlen=int(1.5 / FRAME_S))
        self._frames: deque[bytes] = deque(maxlen=int(KEEP / FRAME_S))
        self.echo = 0.0

    def audio(self) -> bytes:
        return b"".join(self._frames)

    def check(self, level: float, seen: int) -> bool:
        """One frame's loudness; True when the owner is talking over the answer."""
        self._levels.append(level)
        if seen * FRAME_S < LEARN or len(self._levels) <= NEED:
            return False
        before = sorted(list(self._levels)[:-NEED])
        echo = before[int(len(before) * 0.8)] if before else 0.0    # Prometheus's own loud-ish moments
        self.echo = echo
        need = max(echo * OVER_ECHO, self.noise * 4, 700.0)
        return all(v > need for v in list(self._levels)[-NEED:])

    def run(self) -> None:
        seen = 0
        while not self.done.is_set():
            frame = self.mic.read(timeout=0.1)
            if frame is None:
                continue
            seen += 1
            self._frames.append(frame)
            if self.check(rms(frame), seen):
                self.cut = True
                self.stop.set()
                try:
                    self.on_cut()
                except Exception:
                    pass
                return
