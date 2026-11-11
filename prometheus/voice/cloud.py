"""ElevenLabs: a chatbot-level voice and much better hearing (accents, Hinglish, a noisy room).

Hearing: Scribe v2 writes down what was said after the wake word or the double clap (nothing before that leaves
the laptop). Speaking: Flash v2.5 streams the answer back, so the first words play in a fraction of a second.
Both fall back to the local models (Whisper and Kokoro) by themselves when the key, the internet or the
ElevenLabs credits run out, so the voice never just goes silent.

Needs the ELEVENLABS_API_KEY in Settings → Keys.
"""
from __future__ import annotations

import io
import re
import threading
import wave

import httpx

from prometheus.voice.audio import RATE

API = "https://api.elevenlabs.io"
# Newest first; if ElevenLabs refuses one (retired, or not on this plan) the next is used and remembered.
TTS_MODELS = ("eleven_v4_turbo", "eleven_flash_v2_5")   # made for AI assistants: about 0.1 s to the first sound
STT_MODELS = ("scribe_v3", "scribe_v2")
TTS_MODEL, STT_MODEL = TTS_MODELS[0], STT_MODELS[0]
MODEL_REFUSED = (400, 403, 404, 422)
OUT_RATE = 22050                             # pcm_22050: clear speech, small to stream
# (voice id, label) offered in Settings. Any other voice id from the ElevenLabs voice library works too.
VOICES = (
    ("JBFqnCBsd6RMkjVDRZzb", "George: warm British man (default)"),
    ("onwK4e9ZLuTAKqWW03F9", "Daniel: calm British newsreader"),
    ("nPczCjzI2devNBz1zQrb", "Brian: deep and relaxed"),
    ("21m00Tcm4TlvDq8ikWAM", "Rachel: calm woman"),
)
DEFAULT_VOICE = VOICES[0][0]
VOICE_ID = re.compile(r"^[A-Za-z0-9]{10,40}$")
# Words Scribe should expect from the owner: the name, the apps they open, Hinglish they use.
KEYTERMS = ("Prometheus", "Hello Prometheus", "YouTube", "Spotify", "WhatsApp", "GitHub", "Gmail", "flashcards",
            "quiz me", "assignment", "Hinglish", "batao", "kholo")
SOUND_TAG = re.compile(r"\s*[\(\[][^)\]]{1,40}[\)\]]\s*")       # "(laughs)", "[music]": not words


def api_key() -> str:
    from prometheus.config import get_secret

    return (get_secret("ELEVENLABS_API_KEY") or "").strip()


def _wav(audio: bytes, rate: int = RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(audio)
    return out.getvalue()


class ElevenHearing:
    """Same shape as the local Transcriber (.text, .last_confidence, .last_no_speech, .command)."""

    command = True

    def __init__(self, key: str, fallback=None, http: httpx.Client | None = None, language: str | None = None):
        self.key = key
        self.fallback = fallback
        self.language = language          # None: Scribe works out English, Hindi or a mix by itself
        self.http = http or httpx.Client(timeout=httpx.Timeout(10.0, connect=4.0))
        self.last_confidence, self.last_no_speech = 0.0, 0.0
        self.last_engine = "elevenlabs"
        self.failures = 0
        self.model = STT_MODELS[0]

    def text(self, audio: bytes) -> str:
        if not audio:
            self.last_confidence, self.last_no_speech = -9.0, 1.0
            return ""
        try:
            wav = _wav(audio)
            for model in STT_MODELS[STT_MODELS.index(self.model):]:
                data = {"model_id": model, "tag_audio_events": "false", "keyterms": list(KEYTERMS)}
                if self.language:
                    data["language_code"] = self.language
                r = self.http.post(f"{API}/v1/speech-to-text", headers={"xi-api-key": self.key}, data=data,
                                   files={"file": ("speech.wav", wav, "audio/wav")})
                if r.status_code in MODEL_REFUSED and model != STT_MODELS[-1]:
                    continue                      # that model isn't available: the next one
                r.raise_for_status()
                self.model = model
                break
            body = r.json()
        except Exception:
            self.failures += 1
            return self._local(audio)
        self.failures, self.last_engine = 0, "elevenlabs"
        text = SOUND_TAG.sub(" ", str(body.get("text") or "")).strip()
        words = [w for w in body.get("words") or [] if w.get("type", "word") == "word"]
        probs = [float(w["logprob"]) for w in words if isinstance(w.get("logprob"), (int, float))]
        # Scribe's word log-probabilities are near 0 when it is sure; the loop asks again below about -1.15
        self.last_confidence = sum(probs) / len(probs) if probs else (0.0 if text else -9.0)
        self.last_no_speech = 0.0 if text else 1.0
        return text

    def _local(self, audio: bytes) -> str:
        if self.fallback is None:
            self.last_confidence, self.last_no_speech = -9.0, 1.0
            return ""
        self.last_engine = "local"
        text = self.fallback.text(audio)
        self.last_confidence = getattr(self.fallback, "last_confidence", 0.0)
        self.last_no_speech = getattr(self.fallback, "last_no_speech", 0.0)
        return text


class SoundOut:
    """Plays raw 16-bit mono audio as it arrives; abort() stops it at once."""

    def __init__(self, rate: int = OUT_RATE):
        import sounddevice as sd

        self._stream = sd.RawOutputStream(samplerate=rate, channels=1, dtype="int16")
        self._stream.start()

    def write(self, chunk: bytes) -> None:
        self._stream.write(chunk)

    def close(self) -> None:
        try:
            self._stream.stop()
        finally:
            self._stream.close()

    def abort(self) -> None:
        try:
            self._stream.abort()
        except Exception:
            pass


class ElevenSpeaker:
    """Same shape as KokoroSpeaker (.say(text, speed, stop), .ack, .ask, .chime, .stop)."""

    interruptible = True

    def __init__(self, key: str, voice: str = DEFAULT_VOICE, fallback=None, http: httpx.Client | None = None,
                 output=SoundOut):
        self.key = key
        self.voice = voice if VOICE_ID.match(voice or "") else DEFAULT_VOICE
        self.fallback = fallback
        self.http = http or httpx.Client(timeout=httpx.Timeout(20.0, connect=4.0))
        self._output = output
        self._clips: dict[str, bytes] = {}
        self._playing = None
        self._stopped = threading.Event()
        self.last_engine = "elevenlabs"
        self.model = TTS_MODELS[0]

    def _request(self, text: str, speed: float, model: str | None = None):
        speed = max(0.7, min(1.2, speed))
        model = model or self.model
        return self.http.stream(
            "POST", f"{API}/v1/text-to-speech/{self.voice}/stream",
            params={"output_format": f"pcm_{OUT_RATE}", "optimize_streaming_latency": "3"},
            headers={"xi-api-key": self.key, "accept": "audio/pcm"},
            json={"text": text, "model_id": model,
                  "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed}})

    def say(self, text: str, speed: float = 1.0, stop: threading.Event | None = None) -> bool:
        """Speak; returns False if it was stopped before the end."""
        text = (text or "").strip()
        if not text:
            return True
        self._stopped.clear()
        played = False
        try:
            self._pick_model()
            with self._request(text, speed) as resp:
                resp.raise_for_status()
                out = self._output()
                self._playing = out
                spare = b""
                try:
                    for chunk in resp.iter_bytes():
                        if self._stopped.is_set() or (stop is not None and stop.is_set()):
                            out.abort()
                            return False
                        chunk = spare + chunk
                        cut = len(chunk) - len(chunk) % 2           # whole 16-bit samples only
                        chunk, spare = chunk[:cut], chunk[cut:]
                        if chunk:
                            out.write(chunk)
                            played = True
                    out.close()
                finally:
                    self._playing = None
            self.last_engine = "elevenlabs"
            return not (self._stopped.is_set() or (stop is not None and stop.is_set()))
        except Exception:
            if played or self.fallback is None:      # half an answer already spoken: don't start it again
                return played
            self.last_engine = "local"
            return self.fallback.say(text, speed=speed, stop=stop) is not False

    def _pick_model(self) -> None:
        """Once: check the newest voice model works on this account; if refused, use the next one."""
        if getattr(self, "_model_checked", False):
            return
        for model in TTS_MODELS[TTS_MODELS.index(self.model):]:
            with self._request(".", 1.0, model) as resp:
                if resp.status_code in MODEL_REFUSED and model != TTS_MODELS[-1]:
                    continue
                resp.raise_for_status()
                resp.read()
                self.model = model
                break
        self._model_checked = True

    def _clip(self, text: str) -> None:
        """Short fixed phrases ("Yes sir.") are made once and replayed instantly."""
        if text not in self._clips:
            with self._request(text, 1.0) as resp:
                resp.raise_for_status()
                data = b"".join(resp.iter_bytes())
            self._clips[text] = data[: len(data) - len(data) % 2]
        out = self._output()
        out.write(self._clips[text])
        out.close()

    def ack(self) -> None:
        try:
            self._clip("Yes sir.")
        except Exception:
            if self.fallback is not None:
                self.fallback.ack()

    def ask(self) -> None:
        try:
            self._clip("Yes sir?")
        except Exception:
            if self.fallback is not None:
                self.fallback.ask()

    def chime(self) -> None:
        self.ask()

    def stop(self) -> None:
        self._stopped.set()
        out = self._playing
        if out is not None:
            out.abort()
        if self.fallback is not None:
            try:
                self.fallback.stop()
            except Exception:
                pass
