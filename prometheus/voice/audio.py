"""Microphone frames and finding where a spoken command ends."""
from __future__ import annotations

import math
import queue
import threading
from array import array

RATE = 16000
FRAME = 480                     # 30 ms of 16-bit mono audio
FRAME_SECONDS = FRAME / RATE


def rms(frame: bytes) -> float:
    samples = array("h")
    samples.frombytes(frame[: len(frame) - len(frame) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Endpointer:
    """Collects frames of one command: it ends after a pause once speech has started, or gives up if no one speaks."""

    def __init__(self, noise_floor: float = 300.0, pause: float = 0.75, max_seconds: float = 12.0,
                 wait_seconds: float = 5.0):
        self.threshold = max(500.0, noise_floor * 3)
        self.pause_frames = int(pause / FRAME_SECONDS)
        self.max_frames = int(max_seconds / FRAME_SECONDS)
        self.wait_frames = int(wait_seconds / FRAME_SECONDS)
        self.frames: list[bytes] = []
        self.heard = False
        self.quiet = 0

    def feed(self, frame: bytes) -> str:
        """'more', 'done' (speech then a pause, or too long) or 'nothing' (no speech in time)."""
        self.frames.append(frame)
        loud = rms(frame) > self.threshold
        if loud:
            self.heard, self.quiet = True, 0
        else:
            self.quiet += 1
        if len(self.frames) >= self.max_frames:
            return "done" if self.heard else "nothing"
        if self.heard and self.quiet >= self.pause_frames:
            return "done"
        if not self.heard and len(self.frames) >= self.wait_frames:
            return "nothing"
        return "more"

    def audio(self) -> bytes:
        return b"".join(self.frames)


class NoiseFloor:
    """A running estimate of background loudness, so a fan or a busy room doesn't count as speech."""

    def __init__(self):
        self.level = 300.0

    def update(self, frame: bytes) -> float:
        level = rms(frame)
        if level < self.level * 2:
            self.level = 0.98 * self.level + 0.02 * level
        return self.level


class Microphone:
    """The default microphone, 16 kHz mono, as 30 ms frames. Needs the 'sounddevice' package (voice extra)."""

    def __init__(self):
        import sounddevice as sd

        self._q: queue.Queue[bytes] = queue.Queue(maxsize=400)
        self._stream = sd.RawInputStream(samplerate=RATE, channels=1, dtype="int16", blocksize=FRAME,
                                         callback=self._callback)
        self._lock = threading.Lock()

    def _callback(self, data, frames, time_info, status) -> None:
        try:
            self._q.put_nowait(bytes(data))
        except queue.Full:            # never block the audio thread; old audio just drops
            pass

    def start(self) -> None:
        self._stream.start()

    def stop(self) -> None:
        self._stream.stop()
        self._stream.close()

    def read(self, timeout: float = 1.0) -> bytes | None:
        try:
            return self._q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        """Drop audio heard while Prometheus itself was speaking."""
        while True:
            try:
                self._q.get_nowait()
            except queue.Empty:
                return
