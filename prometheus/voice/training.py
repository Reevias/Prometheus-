"""Teaching Prometheus how the owner says "Hello Prometheus" (Settings → Voice → Train wake phrase).

The owner says the phrase 8 times. Each one is transcribed exactly the way the listening loop does it, so we learn:
- how speech-to-text writes the name for this voice ("Permit us", "Prometius"…): those spellings are added as
  wake words, but only when they come right after a greeting ("hello", "hey"…), so ordinary talk stays quiet;
- how loud and how long the owner speaks, so a soft voice or a slow phrase still counts as a try.
The result is kept in <data>/voice/profile.json and used from the next wake on. "Forget training" removes it.
"""
from __future__ import annotations

import difflib
import json
import re
import statistics
import threading
import time
from pathlib import Path

from prometheus.voice.audio import FRAME, RATE, rms

SAMPLES = 8
LIKE = 0.5          # how close a misheard spelling must be to "prometheus" to be learned ("permit us" 0.56)
GREETINGS = {"hello", "hey", "hi", "ok", "okay", "hallo", "hullo", "halo"}
# words that must never become wake words, however the name was misheard
COMMON = set("""a an and are as at be but by can could did do does for from go had has have he her here him his how
i if in is it its just me my no not now of off on one or our out please say she so some that the their them then there
these they this to too up us was we what when where which who why will with would yes you your hello hey hi okay
promise promised promising problem problems permit permission premium promote prompt process program""".split())


def _norm(word: str) -> str:
    return re.sub(r"[^a-z]", "", word.lower())


def _alias(text: str) -> tuple[str, str] | None:
    """('hello', 'permitus') from 'Hello, permit us.' The greeting and the 1–2 words after it."""
    words = [_norm(w) for w in re.findall(r"[A-Za-z']+", text or "")]
    words = [w for w in words if w]
    if len(words) < 2:
        return None
    greeting, rest = words[0], words[1:]
    for n in (2, 1):
        cand = "".join(rest[:n])
        if len(rest) >= n and len(cand) >= 5 and cand not in COMMON \
                and difflib.SequenceMatcher(None, cand, "prometheus").ratio() >= LIKE:
            return greeting, cand
    return None


def learn(samples: list[dict]) -> dict:
    """samples: {'text', 'seconds', 'level', 'vosk'} for each try. Returns the profile."""
    from prometheus.voice.text import has_wake_word

    aliases: dict[str, int] = {}
    greetings = set(GREETINGS)
    for s in samples:
        if has_wake_word(s["text"], learned=False):
            first = _norm((re.findall(r"[A-Za-z']+", s["text"]) or [""])[0])
            if first and first not in COMMON - GREETINGS and len(first) >= 2:
                greetings.add(first)              # "Yellow Prometheus": 'yellow' is how 'hello' is heard
            continue
        got = _alias(s["text"])
        if got:
            if got[0] not in COMMON - GREETINGS:
                greetings.add(got[0])
            aliases[got[1]] = aliases.get(got[1], 0) + 1
    levels = [s["level"] for s in samples if s.get("level")]
    seconds = [s["seconds"] for s in samples if s.get("seconds")]
    profile = {
        "aliases": sorted(aliases),
        "greetings": sorted(greetings),
        "level": round(statistics.median(levels), 1) if levels else 0.0,
        "phrase_seconds": round(statistics.median(seconds), 2) if seconds else 0.0,
        "vosk_hits": sum(1 for s in samples if s.get("vosk")),
        "samples": len(samples),
        "made": time.time(),
    }
    profile["before"] = sum(1 for s in samples if has_wake_word(s["text"], learned=False))
    profile["after"] = sum(1 for s in samples if has_wake_word(s["text"], learned=profile))
    profile["heard"] = [s["text"] for s in samples][:SAMPLES]
    return profile


def thresholds(profile: dict | None) -> dict:
    """What the wake spotter should use for this voice."""
    if not profile or not profile.get("samples"):
        return {}
    out = {}
    if profile.get("level"):
        out["loud"] = max(250.0, min(500.0, 0.35 * profile["level"]))     # soft voices still count as speech
    if profile.get("phrase_seconds"):
        out["burst_max"] = min(8.0, max(4.5, profile["phrase_seconds"] + 3.5))
    return out


class ProfileStore:
    def __init__(self, folder: Path):
        self.path = Path(folder) / "profile.json"

    def load(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def save(self, profile: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(profile, indent=1), encoding="utf-8")

    def forget(self) -> None:
        self.path.unlink(missing_ok=True)


class Trainer:
    """Runs the 8 tries on the live microphone while the normal listening pauses."""

    def __init__(self, service):
        self.service = service
        self.progress = ""
        self.running = False
        self.result: dict | None = None
        self.pause = 0.6                 # seconds between tries

    def start(self) -> bool:
        if self.running:
            return False
        loop = self.service.loop
        if loop is None:
            self.progress = "Turn voice on first (and wait until it says it's listening)."
            return False
        self.running, self.result = True, None
        threading.Thread(target=self._run, args=(loop,), name="prometheus-voice-train", daemon=True).start()
        return True

    def _record_one(self, loop) -> dict:
        from prometheus.voice.audio import Endpointer

        loop.mic.flush()
        ep = Endpointer(noise_floor=loop.noise.level, wait_seconds=6.0, max_seconds=6.0, pause=0.6)
        while True:
            frame = loop.mic.read(timeout=0.5)
            if frame is None:
                continue
            if ep.feed(frame) != "more":
                break
        frames = ep.frames
        loud = [rms(f) for f in frames if rms(f) > ep.threshold]
        audio = ep.audio()
        trigger = getattr(loop.spotter, "_trigger", None)
        vosk = False
        if trigger is not None:
            trigger.reset()
            vosk = any(trigger.feed(f) for f in frames)
            trigger.reset()
        return {"text": loop.transcriber.text(audio) if ep.heard else "",
                "seconds": round(len(loud) * FRAME / RATE, 2), "level": statistics.median(loud) if loud else 0.0,
                "vosk": vosk}

    def _run(self, loop) -> None:
        samples = []
        loop.training = True
        try:
            for i in range(SAMPLES):
                self.progress = f"Say “Hello Prometheus” now ({i + 1} of {SAMPLES})…"
                try:
                    loop._speak_quietly(loop.speaker.chime)
                except Exception:
                    pass
                time.sleep(self.pause / 4)
                samples.append(self._record_one(loop))
                heard = samples[-1]["text"] or "(nothing)"
                self.progress = f"Heard: “{heard}” ({i + 1} of {SAMPLES})"
                time.sleep(self.pause)
            profile = learn([s for s in samples if s["text"]])
            ProfileStore(self.service.models.folder).save(profile)
            self.service.apply_profile(profile)
            self.result = profile
            self.progress = (f"Done. Before training I'd have woken {profile['before']} of {profile['samples']} "
                             f"times; now {profile['after']} of {profile['samples']}.")
        except Exception as e:
            self.progress = f"Training stopped: {type(e).__name__}: {e}"
        finally:
            loop.training = False
            self.running = False
