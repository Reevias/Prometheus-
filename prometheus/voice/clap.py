"""Hearing a double clap: two short, sharp bangs a moment apart.

A clap is different from talking, music or a door: it jumps from quiet to very loud within one 30 ms frame and
dies away again within about 150 ms. Two of those, 0.12 to 0.8 seconds apart, with quiet in between and no third
one straight after, count as a double clap. Talking never has that shape, so it doesn't fire on conversation.
"""
from __future__ import annotations

from array import array

from prometheus.voice.audio import FRAME, RATE, rms

FRAME_S = FRAME / RATE
MIN_GAP, MAX_GAP = 0.12, 0.8       # seconds between the two claps
DECAY_FRAMES = 5                    # a clap has died down within this many frames (150 ms)
COOLDOWN = 2.5                      # seconds before another double clap can count


def peak(frame: bytes) -> int:
    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % 2])
    return max((abs(s) for s in samples), default=0)


class ClapDetector:
    """feed(frame) -> True once, at the end of a double clap."""

    def __init__(self, sensitivity: float = 1.0):
        self.sensitivity = max(0.3, min(3.0, sensitivity))    # higher hears softer claps
        self.noise = 200.0
        self.prev = 0.0
        self.frame_no = 0
        self._onset = -1          # frame of the current bang
        self._onset_level = 0.0
        self._claps: list[int] = []     # frames where real claps started
        self._last_fire = -10_000

    def reset(self) -> None:
        self._onset = -1
        self._claps.clear()

    def _loud_enough(self, level: float, top: int) -> bool:
        need = max(2500.0 / self.sensitivity, self.noise * 10 / self.sensitivity)
        return level > need and top > 9000 / self.sensitivity

    def feed(self, frame: bytes) -> bool:
        self.frame_no += 1
        n = self.frame_no
        level = rms(frame)
        fired = False
        if self._onset >= 0:
            self._onset_level = max(self._onset_level, level) if n - self._onset <= 1 else self._onset_level
            if n - self._onset >= 2 and level < self._onset_level * 0.3:
                self._claps.append(self._onset)        # it died away fast: a clap
                self._onset = -1
            elif n - self._onset > DECAY_FRAMES:
                self._onset = -1                        # still loud: talking, music, a door — not a clap
                self._claps.clear()
        elif self._loud_enough(level, peak(frame)) and level > max(self.prev, self.noise) * 4:
            self._onset, self._onset_level = n, level  # a sudden bang out of quiet
        # the room's background loudness, learned only from ordinary frames
        if self._onset < 0 and level < self.noise * 3:
            self.noise = 0.98 * self.noise + 0.02 * max(level, 50.0)
        self.prev = level
        # drop claps that are too old to pair
        self._claps = [c for c in self._claps if (n - c) * FRAME_S <= MAX_GAP + 0.4]
        if len(self._claps) >= 3:
            self._claps = []                            # three or more bangs: knocking or applause
        elif len(self._claps) == 2:
            a, b = self._claps
            gap = (b - a) * FRAME_S
            if gap > MAX_GAP:
                self._claps = [b]
            elif gap < MIN_GAP:
                self._claps = [a]                       # one clap's echo, not a second clap
            elif (n - b) * FRAME_S >= 0.35 and self._onset < 0:
                # 0.35 s of no third bang: it was exactly two
                if (n - self._last_fire) * FRAME_S >= COOLDOWN:
                    self._last_fire = n
                    fired = True
                self._claps = []
        return fired
