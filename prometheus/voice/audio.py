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


class MicError(Exception):
    """No microphone could be opened; the message says what to check, in plain words."""


MIC_HELP = ("I couldn't open a microphone. Check three things: 1) Windows Settings → Privacy & security → Microphone: "
            "turn on \"Microphone access\" and \"Let desktop apps access your microphone\". 2) A microphone is plugged "
            "in and picked under Settings → System → Sound → Input. 3) No other app (a call, a recorder) is holding it. "
            "Then switch \"Listen for Prometheus\" off and on.")
HOST_ORDER = ("Windows WASAPI", "Windows DirectSound", "MME", "Windows WDM-KS")   # most reliable first


def mic_candidates(sd) -> list[tuple[int | None, int]]:
    """Ways to open a microphone, best first: (device or None for the default, sample rate). Windows offers each
    microphone through several sound systems; when one refuses ("MME error 1"), another usually works. A device that
    can't record at 16 kHz is opened at its own rate and the sound is converted."""
    out: list[tuple[int | None, int]] = [(None, RATE)]
    try:
        devices, apis = list(sd.query_devices()), list(sd.query_hostapis())
    except Exception:
        return out
    try:
        default_in = sd.default.device[0]
    except Exception:
        default_in = -1
    ranked = []
    for i, dev in enumerate(devices):
        if int(dev.get("max_input_channels", 0)) < 1:
            continue
        api = apis[dev["hostapi"]]["name"] if dev.get("hostapi", -1) < len(apis) else ""
        order = HOST_ORDER.index(api) if api in HOST_ORDER else len(HOST_ORDER)
        ranked.append((0 if i == default_in else 1, order, i, int(dev.get("default_samplerate") or 0)))
    for _, _, i, native in sorted(ranked):
        out.append((i, RATE))
        if native and native != RATE:
            out.append((i, native))
    return out


def _resample_plain(data: bytes, out_len: int) -> bytes:
    """Linear resampling of 16-bit mono sound to out_len samples, in plain Python (about 1500 samples a frame)."""
    from array import array

    src = array("h")
    src.frombytes(data[: len(data) - len(data) % 2])
    n = len(src)
    if n < 2 or out_len < 1:
        return b""
    out = array("h", bytes(2 * out_len))
    step = (n - 1) / (out_len - 1) if out_len > 1 else 0.0
    for i in range(out_len):
        x = i * step
        j = int(x)
        if j >= n - 1:
            out[i] = src[n - 1]
        else:
            f = x - j
            out[i] = int(src[j] + (src[j + 1] - src[j]) * f)
    return out.tobytes()


class Microphone:
    """A microphone as 16 kHz mono 30 ms frames. Tries every way Windows offers to open one before giving up.
    Needs the 'sounddevice' package (voice extra)."""

    def __init__(self, sd=None):
        if sd is None:
            import sounddevice as sd

        self._q: queue.Queue[bytes] = queue.Queue(maxsize=400)
        self._lock = threading.Lock()
        self.rate = RATE
        self.device = None
        self.tried: list[str] = []
        self._stream = None
        for device, rate in mic_candidates(sd):
            try:
                self._stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", device=device,
                                                 blocksize=int(rate * FRAME / RATE), callback=self._callback)
                self.rate, self.device = rate, device
                break
            except Exception as e:           # this way is refused: try the next
                self.tried.append(f"{'default' if device is None else device}@{rate}: {str(e)[:80]}")
        if self._stream is None:
            raise MicError(MIC_HELP)

    def _to_16k(self, data: bytes) -> bytes:
        """Sound recorded at the microphone's own rate (44.1 or 48 kHz), converted to one 16 kHz frame."""
        try:
            import numpy as np
        except ImportError:          # the raw microphone stream doesn't need numpy, so this must work without it
            return _resample_plain(data, FRAME)

        src = np.frombuffer(data, dtype=np.int16)
        if len(src) < 2:
            return b""
        at = np.linspace(0, len(src) - 1, FRAME)
        return np.interp(at, np.arange(len(src)), src).astype(np.int16).tobytes()

    def _callback(self, data, frames, time_info, status) -> None:
        try:
            frame = bytes(data) if self.rate == RATE else self._to_16k(bytes(data))
            if frame:
                self._q.put_nowait(frame)
        except queue.Full:            # never block the audio thread; old audio just drops
            pass
        except Exception:
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
