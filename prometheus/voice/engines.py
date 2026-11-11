"""The three voice models, all local: Vosk hears the wake word, Whisper writes down the command, Piper speaks.

Wake word in two stages, tested with spoken audio: Vosk (tiny, always on) fires on anything that sounds like
"Prometheus"; Whisper then checks the last couple of seconds and only a real "Prometheus" wakes it. That keeps the
always-on part cheap and stops "promising" or "the problem" from waking it.
"""
from __future__ import annotations

import io
import json
import threading
import zipfile
from pathlib import Path

import httpx

from prometheus.voice.audio import FRAME, RATE, rms
from prometheus.voice.text import has_wake_word

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_DIR = "vosk-model-small-en-us-0.15"
PIPER_VOICE = "en_GB-alan-medium"            # a calm British voice
PIPER_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/"
             f"{PIPER_VOICE}.onnx")
KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
KOKORO_MODEL, KOKORO_VOICES = "kokoro-v1.0.int8.onnx", "voices-v1.0.bin"
# The voices offered in Settings: (id, label). Kokoro sounds far more human than Piper.
VOICES = (("af_heart", "Heart: warm, calm (default)"), ("bm_fable", "Fable: British male, light"))
DEFAULT_VOICE = "af_heart"
WHISPER_SIZE = "base.en"                     # tiny.en missed real wake words in testing; base.en got them all
# How commands are turned into words (Settings → Voice). The wake phrase always uses base.en (fast).
SPEECH = {
    "fast": ("base.en", "en", "Fast English (already downloaded)"),
    "accent": ("small.en", "en", "Accurate English, better with Indian accents (460 MB, about a second slower)"),
    "hindi": ("small", None, "English and Hindi, Hinglish (460 MB, about a second slower)"),
}
DEFAULT_SPEECH = "fast"
# Biases Whisper towards how the owner talks: the name, everyday commands, and Hinglish in Latin letters.
COMMAND_PROMPT = ("Hello Prometheus. Open YouTube. What time is it? Set a timer for ten minutes. "
                  "Kal ka weather batao. Spotify kholo.")
DOWNLOAD_HOSTS = ("alphacephei.com", "huggingface.co", "github.com")
CONFIRM_AFTER = 0.8                          # seconds of audio after Vosk fires before Whisper checks
CONFIRM_BEFORE = 2.0
COOLDOWN = 1.5
BURST_MIN, BURST_MAX = 0.3, 4.5               # seconds of speech that could be "Prometheus, <short command>"
BURST_PAUSE = 0.45                           # quiet that ends a burst
CONFIRM_LONG = 3.0                           # Vosk fired but the talking goes on: check after this long


class VoiceNotReady(Exception):
    pass


class VoiceModels:
    """Where the models live (<data>/voice) and how to get them."""

    def __init__(self, folder: Path, speech: str = DEFAULT_SPEECH):
        self.folder = Path(folder)
        self.speech = speech if speech in SPEECH else DEFAULT_SPEECH

    @property
    def vosk(self) -> Path:
        return self.folder / VOSK_DIR

    @property
    def piper(self) -> Path:
        return self.folder / f"{PIPER_VOICE}.onnx"

    @property
    def kokoro(self) -> Path:
        return self.folder / KOKORO_MODEL

    @property
    def kokoro_voices(self) -> Path:
        return self.folder / KOKORO_VOICES

    @property
    def whisper(self) -> Path:
        return self.folder / "whisper" / WHISPER_SIZE

    @property
    def command_whisper(self) -> Path:
        return self.folder / "whisper" / SPEECH[self.speech][0]

    def missing(self) -> list[str]:
        out = []
        if not (self.vosk / "am").exists():
            out.append("wake word (Vosk, 40 MB)")
        if not (self.whisper / "model.bin").exists():
            out.append("speech to text (Whisper, 145 MB)")
        if not (self.kokoro.exists() and self.kokoro_voices.exists()):
            out.append("voice (Kokoro, 120 MB)")
        if self.command_whisper != self.whisper and not (self.command_whisper / "model.bin").exists():
            out.append(f"better speech to text ({SPEECH[self.speech][0]}, 460 MB)")
        return out

    def packages_missing(self) -> list[str]:
        out = []
        for module, package in (("vosk", "vosk"), ("faster_whisper", "faster-whisper"), ("kokoro_onnx", "kokoro-onnx"),
                                ("sounddevice", "sounddevice"), ("numpy", "numpy")):
            try:
                __import__(module)
            except Exception:       # missing, or the sound system isn't there
                out.append(package)
        return out

    def download(self, progress=lambda text: None, http: httpx.Client | None = None) -> None:
        """Fetch whatever is missing. Only from the two known hosts; the zip is unpacked safely."""
        self.folder.mkdir(parents=True, exist_ok=True)
        http = http or httpx.Client(timeout=httpx.Timeout(600.0, connect=20.0), follow_redirects=True)
        if not (self.vosk / "am").exists():
            progress("Downloading the wake word model (40 MB)…")
            data = _get(http, VOSK_URL)
            _safe_unzip(data, self.folder)
        if not (self.kokoro.exists() and self.kokoro_voices.exists()):
            progress("Downloading the voice (120 MB)…")
            for name, target in ((KOKORO_MODEL, self.kokoro), (KOKORO_VOICES, self.kokoro_voices)):
                tmp = target.with_suffix(target.suffix + ".part")
                tmp.write_bytes(_get(http, KOKORO_BASE + name))
                tmp.replace(target)
        if not (self.whisper / "model.bin").exists():
            progress("Downloading speech to text (145 MB)…")
            from faster_whisper.utils import download_model

            self.whisper.mkdir(parents=True, exist_ok=True)
            download_model(WHISPER_SIZE, output_dir=str(self.whisper))
        if self.command_whisper != self.whisper and not (self.command_whisper / "model.bin").exists():
            progress("Downloading better speech to text (460 MB)…")
            from faster_whisper.utils import download_model

            self.command_whisper.mkdir(parents=True, exist_ok=True)
            download_model(SPEECH[self.speech][0], output_dir=str(self.command_whisper))
        progress("Voice is ready.")


def _get(http: httpx.Client, url: str) -> bytes:
    from urllib.parse import urlsplit

    if (urlsplit(url).hostname or "") not in DOWNLOAD_HOSTS:
        raise VoiceNotReady(f"Refusing to download from {url}")
    resp = http.get(url)
    resp.raise_for_status()
    return resp.content


def _safe_unzip(data: bytes, folder: Path) -> None:
    folder = folder.resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for member in z.infolist():
            target = (folder / member.filename).resolve()
            if folder not in target.parents and target != folder:
                raise VoiceNotReady("The download had a file outside its folder; it was not unpacked.")
        z.extractall(folder)


class Transcriber:
    """Whisper base.en on the CPU (int8): about half a second for a short command."""

    def __init__(self, models: VoiceModels, command: bool = False):
        """command=False: the fast wake-phrase model. command=True: the model chosen for commands in Settings."""
        from faster_whisper import WhisperModel

        import os

        threads = max(2, min(4, (os.cpu_count() or 4) // 2))       # faster, but leaves room for everything else
        path = models.command_whisper if command else models.whisper
        self.language = SPEECH[models.speech][1] if command else "en"
        self.command = command
        self._model = WhisperModel(str(path), device="cpu", compute_type="int8", cpu_threads=threads)
        self._lock = threading.Lock()

    def text(self, audio: bytes) -> str:
        import numpy as np

        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            # The hint makes Whisper expect the name, so it stops writing "Promy Fierce" for "Prometheus".
            # Commands get a wider search (beam 3) and a prompt with everyday commands: more accurate words.
            segments, _ = self._model.transcribe(samples, language=self.language, beam_size=3 if self.command else 1,
                                                 without_timestamps=True, condition_on_previous_text=False,
                                                 hotwords="Hello Prometheus",
                                                 initial_prompt=COMMAND_PROMPT if self.command else None,
                                                 vad_filter=self.command)
            return " ".join(s.text for s in segments).strip()


class VoskTrigger:
    """The cheap first stage: a recogniser that only knows the word "prometheus"."""

    def __init__(self, models: VoiceModels):
        import vosk

        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(models.vosk))
        self._rec = vosk.KaldiRecognizer(self._model, RATE,
                                         json.dumps(["hello prometheus", "hey prometheus", "prometheus", "[unk]"]))

    def feed(self, frame: bytes) -> bool:
        self._rec.AcceptWaveform(frame)
        return "prometheus" in json.loads(self._rec.PartialResult()).get("partial", "").split()

    def reset(self) -> None:
        self._rec.Reset()


class WakeSpotter:
    """Feed it 30 ms frames. It returns the audio around the wake word when "Prometheus" was really said.

    Two ways in, both confirmed by Whisper: Vosk fires on something like the word (fast), or a short burst of
    speech ends (0.3 to 4.5 s, the length of "Prometheus, open YouTube") that Vosk missed, for example because of
    an accent. Whisper hearing the name is what wakes it, so ordinary talk still doesn't.
    """

    def __init__(self, models: VoiceModels | None, transcriber, trigger=None):
        self._trigger = trigger if trigger is not None else VoskTrigger(models)
        self._confirm = transcriber
        self._recent: list[bytes] = []
        self._pending = -1
        self._since = 10_000
        self._noise = 300.0
        self._burst = 0           # frames since the current burst of speech started
        self._quiet = 0           # quiet frames at the end of it
        self._vosk_in_burst = False
        self.loud_min = 500.0        # loudness that counts as speech (lower for a soft voice, after training)
        self.burst_max = BURST_MAX   # longest burst worth checking (longer for a slow speaker, after training)
        self.last_text = ""          # what Whisper heard around the wake word
        self.phrase_done = False     # True when that check ran after the speaker paused (the command is complete)
        self.checks = 0              # how many times Whisper was asked (for the voice test)

    def reset(self) -> None:
        self._trigger.reset()
        self._recent.clear()
        self._pending = -1
        self._burst = self._quiet = 0
        self._vosk_in_burst = False

    def _check(self, frames: int) -> bytes | None:
        audio = b"".join(self._recent[-frames:])
        self._pending, self._since = -1, 0
        self._burst = self._quiet = 0
        self._vosk_in_burst = False
        self._trigger.reset()
        self.checks += 1
        self.last_text = self._confirm.text(audio)
        return audio if has_wake_word(self.last_text) else None

    def feed(self, frame: bytes) -> bytes | None:
        self._recent.append(frame)
        keep = int((max(self.burst_max, BURST_MAX) + 1.5) * RATE / FRAME)
        if len(self._recent) > keep:
            del self._recent[: len(self._recent) - keep]
        self._since += 1
        level = rms(frame)
        loud = level > max(self.loud_min, self._noise * 3)
        if not loud:
            self._noise = 0.98 * self._noise + 0.02 * level
        fired = self._trigger.feed(frame)
        cooled = self._since * FRAME / RATE > COOLDOWN

        # Vosk hearing something like the name marks this burst of speech as worth checking.
        if fired and cooled and self._pending < 0:
            self._pending = 0
            self._vosk_in_burst = True
        elif self._pending >= 0:
            self._pending += 1
        lead = int(0.4 * RATE / FRAME)
        if loud:
            self._burst += 1
            self._quiet = 0
        elif self._burst:
            self._burst += 1
            self._quiet += 1
        # Whisper checks the WHOLE phrase once the speaker pauses ("Prometheus, open YouTube" is heard far more
        # reliably than "Prometheus" cut off on its own). A long ramble flagged by Vosk is checked after a while.
        ended = self._burst and self._quiet * FRAME / RATE >= BURST_PAUSE
        if self._pending >= 0 and (ended or self._pending * FRAME / RATE >= CONFIRM_LONG):
            self.phrase_done = bool(ended)
            frames = min(len(self._recent), (self._burst or self._pending) + lead)
            return self._check(max(frames, int((CONFIRM_BEFORE + CONFIRM_AFTER) * RATE / FRAME)))
        if ended:
            spoken = (self._burst - self._quiet) * FRAME / RATE
            frames = self._burst + lead
            if BURST_MIN <= spoken <= self.burst_max and cooled:       # Vosk missed it (an accent): check anyway
                self.phrase_done = True
                return self._check(frames)
            self._burst = self._quiet = 0
            self._vosk_in_burst = False
        return None


class KokoroSpeaker:
    """A natural local voice (Kokoro). Long answers are spoken sentence by sentence: the first one starts while
    the rest is still being made. "Yes sir" is made once at start, so it plays instantly."""

    def __init__(self, models: VoiceModels, voice: str = DEFAULT_VOICE):
        from kokoro_onnx import Kokoro

        self._tts = Kokoro(str(models.kokoro), str(models.kokoro_voices))
        self.voice = voice if voice in dict(VOICES) else DEFAULT_VOICE
        self._lang = "en-gb" if self.voice.startswith("b") else "en-us"
        self._ack = self._make("Yes sir.")
        self._ask = self._make("Yes sir?")

    def _make(self, text: str):
        import numpy as np

        audio, rate = self._tts.create(text, voice=self.voice, speed=1.0, lang=self._lang)
        return (np.clip(audio, -1, 1) * 32767).astype(np.int16), rate

    @staticmethod
    def _play(clip) -> None:
        import sounddevice as sd

        sd.play(clip[0], clip[1])
        sd.wait()

    def ack(self) -> None:
        self._play(self._ack)

    def ask(self) -> None:
        self._play(self._ask)

    def chime(self) -> None:
        self.ask()

    def say(self, text: str) -> None:
        import queue
        import re

        parts = [p for p in re.split(r"(?<=[.!?])\s+", text.strip()) if p]
        if not parts:
            return
        ready: queue.Queue = queue.Queue()

        def make_all():
            for p in parts:
                ready.put(self._make(p))
            ready.put(None)

        threading.Thread(target=make_all, daemon=True).start()
        while (clip := ready.get()) is not None:
            self._play(clip)


class PiperSpeaker:
    """Speaks with the local Piper voice through the default speakers."""

    def __init__(self, models: VoiceModels):
        from piper import PiperVoice

        self._voice = PiperVoice.load(str(models.piper))

    def say(self, text: str) -> None:
        import numpy as np
        import sounddevice as sd

        chunks = list(self._voice.synthesize(text))
        if not chunks:
            return
        audio = np.concatenate([c.audio_int16_array for c in chunks])
        sd.play(audio, chunks[0].sample_rate)
        sd.wait()

    def chime(self) -> None:
        import numpy as np
        import sounddevice as sd

        t = np.linspace(0, 0.12, int(RATE * 0.12), endpoint=False)
        tone = (np.sin(2 * np.pi * 880 * t) * np.hanning(len(t)) * 0.25 * 32767).astype(np.int16)
        sd.play(tone, RATE)
        sd.wait()

