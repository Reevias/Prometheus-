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

from prometheus.voice.audio import FRAME, RATE
from prometheus.voice.text import has_wake_word

VOSK_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
VOSK_DIR = "vosk-model-small-en-us-0.15"
PIPER_VOICE = "en_GB-alan-medium"            # a calm British voice
PIPER_URL = ("https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_GB/alan/medium/"
             f"{PIPER_VOICE}.onnx")
WHISPER_SIZE = "base.en"                     # tiny.en missed real wake words in testing; base.en got them all
DOWNLOAD_HOSTS = ("alphacephei.com", "huggingface.co")
CONFIRM_AFTER = 0.8                          # seconds of audio after Vosk fires before Whisper checks
CONFIRM_BEFORE = 2.0
COOLDOWN = 1.5


class VoiceNotReady(Exception):
    pass


class VoiceModels:
    """Where the models live (<data>/voice) and how to get them."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)

    @property
    def vosk(self) -> Path:
        return self.folder / VOSK_DIR

    @property
    def piper(self) -> Path:
        return self.folder / f"{PIPER_VOICE}.onnx"

    @property
    def whisper(self) -> Path:
        return self.folder / "whisper" / WHISPER_SIZE

    def missing(self) -> list[str]:
        out = []
        if not (self.vosk / "am").exists():
            out.append("wake word (Vosk, 40 MB)")
        if not (self.whisper / "model.bin").exists():
            out.append("speech to text (Whisper, 145 MB)")
        if not (self.piper.exists() and Path(str(self.piper) + ".json").exists()):
            out.append("voice (Piper, 63 MB)")
        return out

    def packages_missing(self) -> list[str]:
        out = []
        for module, package in (("vosk", "vosk"), ("faster_whisper", "faster-whisper"), ("piper", "piper-tts"),
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
        if not (self.piper.exists() and Path(str(self.piper) + ".json").exists()):
            progress("Downloading the voice (63 MB)…")
            for url, target in ((PIPER_URL, self.piper), (PIPER_URL + ".json", Path(str(self.piper) + ".json"))):
                tmp = target.with_suffix(target.suffix + ".part")
                tmp.write_bytes(_get(http, url))
                tmp.replace(target)
        if not (self.whisper / "model.bin").exists():
            progress("Downloading speech to text (145 MB)…")
            from faster_whisper.utils import download_model

            self.whisper.mkdir(parents=True, exist_ok=True)
            download_model(WHISPER_SIZE, output_dir=str(self.whisper))
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

    def __init__(self, models: VoiceModels):
        from faster_whisper import WhisperModel

        self._model = WhisperModel(str(models.whisper), device="cpu", compute_type="int8", cpu_threads=2)
        self._lock = threading.Lock()

    def text(self, audio: bytes) -> str:
        import numpy as np

        samples = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0
        with self._lock:
            segments, _ = self._model.transcribe(samples, language="en", beam_size=1, without_timestamps=True,
                                                 condition_on_previous_text=False)
            return " ".join(s.text for s in segments).strip()


class WakeSpotter:
    """Feed it 30 ms frames. It returns the audio from just before the wake word when "Prometheus" was really said."""

    def __init__(self, models: VoiceModels, transcriber: Transcriber):
        import vosk

        vosk.SetLogLevel(-1)
        self._model = vosk.Model(str(models.vosk))
        self._rec = vosk.KaldiRecognizer(self._model, RATE, json.dumps(["prometheus", "[unk]"]))
        self._confirm = transcriber
        self._recent: list[bytes] = []
        self._pending = -1
        self._since = 10_000
        self.last_text = ""          # what Whisper heard around the wake word

    def reset(self) -> None:
        self._rec.Reset()
        self._recent.clear()
        self._pending = -1

    def feed(self, frame: bytes) -> bytes | None:
        self._recent.append(frame)
        keep = int((CONFIRM_BEFORE + CONFIRM_AFTER + 1.0) * RATE / FRAME)
        if len(self._recent) > keep:
            del self._recent[: len(self._recent) - keep]
        self._since += 1
        self._rec.AcceptWaveform(frame)
        if self._pending < 0:
            partial = json.loads(self._rec.PartialResult()).get("partial", "")
            if "prometheus" in partial.split() and self._since * FRAME / RATE > COOLDOWN:
                # Vosk only knows one word, so it fires on anything like it; Whisper decides below
                self._pending = 0
            return None
        self._pending += 1
        if self._pending * FRAME / RATE < CONFIRM_AFTER:
            return None
        window = int((CONFIRM_BEFORE + CONFIRM_AFTER) * RATE / FRAME)
        audio = b"".join(self._recent[-window:])
        self._pending, self._since = -1, 0
        self._rec.Reset()
        self.last_text = self._confirm.text(audio)
        return audio if has_wake_word(self.last_text) else None


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

