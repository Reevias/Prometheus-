"""The chatbot-level voice: ElevenLabs hearing and speaking (with the local fallback), the double clap, the
brief, the clap conversation, and talking over an answer."""
import json
import math
import random
import threading
from array import array
from datetime import datetime
from types import SimpleNamespace

import httpx

from prometheus.voice.audio import FRAME, RATE
from prometheus.voice.bargein import BargeIn
from prometheus.voice.clap import ClapDetector
from prometheus.voice.cloud import ElevenHearing, ElevenSpeaker
from prometheus.voice.loop import VoiceLoop
from tests.test_v14_voice import FakeMic, FakeSpotter, FakeTranscriber, tone

rng = random.Random(7)


def _cl(x):
    return max(-32767, min(32767, int(x)))


def silence(s):
    return [_cl(rng.gauss(0, 80)) for _ in range(int(RATE * s))]


def clap():
    return [_cl(rng.gauss(0, 1) * 20000 * math.exp(-i / (RATE * 0.015))) for i in range(int(RATE * 0.12))]


def speech(s, amp=12000):
    out = []
    for i in range(int(RATE * s)):
        t = i / RATE
        env = min(1, t / 0.05) * (0.6 + 0.4 * math.sin(2 * math.pi * 4 * t))
        out.append(_cl(math.sin(2 * math.pi * 180 * t) * env * amp + rng.gauss(0, 300)))
    return out


def frames(*parts):
    b = array("h", sum(parts, [])).tobytes()
    return [b[i:i + FRAME * 2] for i in range(0, len(b) - FRAME * 2 + 1, FRAME * 2)]


def fires(*parts):
    d = ClapDetector()
    return sum(1 for f in frames(*parts) if d.feed(f))


# -- the double clap ----------------------------------------------------------------------------

def test_two_claps_wake_it_but_talking_one_clap_or_knocking_do_not():
    assert fires(silence(1), clap(), silence(0.25), clap(), silence(1.5)) == 1
    assert fires(silence(1), clap(), silence(0.45), clap(), silence(1.5)) == 1
    assert fires(silence(1), clap(), silence(2)) == 0                                        # one clap
    assert fires(silence(1), clap(), silence(0.2), clap(), silence(0.2), clap(), silence(2)) == 0   # knocking
    assert fires(silence(1), clap(), silence(1.3), clap(), silence(2)) == 0                  # too far apart
    assert fires(silence(1), speech(1.5), silence(0.3), speech(0.8), silence(1)) == 0        # talking
    assert fires(silence(1), speech(0.1), silence(0.25), speech(0.1), silence(1)) == 0       # two short words


# -- ElevenLabs hearing -------------------------------------------------------------------------

def test_scribe_hears_the_command_and_says_how_sure_it_was():
    seen = {}

    def handler(request):
        seen["key"] = request.headers["xi-api-key"]
        seen["body"] = request.content
        return httpx.Response(200, json={"text": "What's the weather (laughs) tomorrow?", "words": [
            {"text": "What's", "type": "word", "logprob": -0.1}, {"text": " ", "type": "spacing"},
            {"text": "weather", "type": "word", "logprob": -0.3}]})

    ear = ElevenHearing("k-123", http=httpx.Client(transport=httpx.MockTransport(handler)))
    assert ear.text(b"\x00\x01" * 1600) == "What's the weather tomorrow?"
    assert seen["key"] == "k-123" and b"scribe_v3" in seen["body"] and b"Prometheus" in seen["body"]
    assert b"RIFF" in seen["body"]                                       # sent as a proper WAV file
    assert -0.3 < ear.last_confidence < 0 and ear.last_no_speech == 0.0 and ear.command


def test_scribe_down_falls_back_to_the_laptop():
    class Local:
        last_confidence, last_no_speech = -0.2, 0.1

        def text(self, audio):
            return "open youtube"

    ear = ElevenHearing("k", fallback=Local(),
                        http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(401))))
    assert ear.text(b"\x00" * 3200) == "open youtube" and ear.last_engine == "local"
    assert ear.last_confidence == -0.2


# -- ElevenLabs speaking -------------------------------------------------------------------------

class FakeOut:
    played: list = []

    def __init__(self):
        self.chunks, self.aborted, self.closed = [], False, False
        FakeOut.played.append(self)

    def write(self, chunk):
        assert len(chunk) % 2 == 0           # only whole samples ever reach the speakers
        self.chunks.append(chunk)

    def close(self):
        self.closed = True

    def abort(self):
        self.aborted = True


def test_voice_streams_whole_samples_and_falls_back_when_down():
    FakeOut.played = []
    sent = {}

    def handler(request):
        sent.update(json.loads(request.content))
        return httpx.Response(200, stream=httpx.ByteStream(b"\x01\x02\x03" * 101))

    mouth = ElevenSpeaker("k", "JBFqnCBsd6RMkjVDRZzb", http=httpx.Client(transport=httpx.MockTransport(handler)),
                          output=FakeOut)
    assert mouth.say("Hello sir.", speed=1.5) is True
    assert sent["model_id"] == "eleven_v4_turbo" and sent["voice_settings"]["speed"] == 1.2   # kept in range
    assert FakeOut.played[-1].closed and sum(len(c) for c in FakeOut.played[-1].chunks) == 302

    class Local:
        said = []

        def say(self, text, speed=1.0, stop=None):
            self.said.append(text)
            return True

    local = Local()
    down = ElevenSpeaker("k", fallback=local, output=FakeOut,
                         http=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(402))))
    assert down.say("Out of credits.") is True and local.said == ["Out of credits."] and down.last_engine == "local"
    assert ElevenSpeaker("k", "not a voice!").voice == "JBFqnCBsd6RMkjVDRZzb"


def test_voice_stops_when_told():
    FakeOut.played = []
    stop = threading.Event()
    stop.set()
    mouth = ElevenSpeaker("k", output=FakeOut, http=httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, stream=httpx.ByteStream(b"\x00" * 400)))))
    assert mouth.say("A long answer.", stop=stop) is False and FakeOut.played[-1].aborted


# -- talking over an answer ----------------------------------------------------------------------

def test_cutting_in_works_on_headphones_and_ignores_the_echo_on_speakers():
    def feed(levels):
        b = BargeIn(None, 200.0, threading.Event())
        return [b.check(v, i + 1) for i, v in enumerate(levels)]

    # headphones: the mic hears almost nothing of the answer, so normal talking cuts in
    assert any(feed([250] * 30 + [2500] * 10))
    # laptop speakers: its own voice is loud in the mic; that alone never cuts in…
    echo = [3000 + 1500 * math.sin(i) for i in range(80)]
    assert not any(feed(echo))
    # …but the owner talking clearly louder than the echo does
    assert any(feed(echo + [12000] * 10))
    # a cough (two loud frames) doesn't
    assert not any(feed([250] * 30 + [5000] * 2 + [250] * 10))


class StoppableSpeaker:
    interruptible = True

    def __init__(self):
        self.said, self.stopped = [], threading.Event()

    def say(self, text, speed=1.0, stop=None):
        self.said.append(text)
        if stop is not None:
            stop.wait(timeout=3)
            return not stop.is_set()
        return True

    def stop(self):
        self.stopped.set()

    def ask(self):
        pass

    def ack(self):
        pass


def test_talking_over_the_answer_stops_it_and_keeps_what_was_said():
    mic = FakeMic(frames(silence(0.6), speech(1.0, amp=14000)))      # headphones: quiet, then the owner talks
    loop = VoiceLoop(mic, FakeSpotter(10 ** 6), FakeTranscriber([]), StoppableSpeaker(), lambda c: "")
    loop.noise.level = 150.0
    assert loop._say("A very long answer about the weather.", barge=True) is True
    assert loop.speaker.stopped.is_set() and len(loop._barged) > FRAME * 2 * 5


# -- the clap conversation --------------------------------------------------------------------------

class Speaker:
    def __init__(self):
        self.said = []

    def say(self, text):
        self.said.append(text)

    def ask(self):
        pass

    def ack(self):
        pass

    def chime(self):
        pass


def test_double_clap_greets_gives_the_brief_then_just_talks():
    claps = frames(silence(0.5), clap(), silence(0.25), clap(), silence(0.6))
    talk = tone(1.0) + tone(1.2, loud=False) + tone(1.0) + tone(1.2, loud=False)
    mic, speaker, asked = FakeMic(claps + talk), Speaker(), []
    loop = VoiceLoop(mic, FakeSpotter(10 ** 6), FakeTranscriber(["what's the weather tomorrow", "that's all"]),
                     speaker, lambda c: (asked.append(c), "Rain after four.")[1], clap=ClapDetector(),
                     on_clap=lambda: "Good evening sir. It's 7:40 PM. What would you like to do?")
    loop._stop.set = lambda: None
    threading.Thread(target=loop.run, daemon=True).start()
    for _ in range(200):
        if "Alright sir." in speaker.said:
            break
        threading.Event().wait(0.05)
    loop._stop = threading.Event()
    loop._stop.set()
    assert speaker.said[0].startswith("Good evening sir.")
    assert asked == ["What's the weather tomorrow"]               # no "Prometheus" needed
    assert "Rain after four." in speaker.said and speaker.said[-1] == "Alright sir."


def test_quiet_after_the_brief_ends_the_conversation_politely():
    mic, speaker = FakeMic(tone(0.2, loud=False)), Speaker()
    loop = VoiceLoop(mic, FakeSpotter(10 ** 6), FakeTranscriber([]), speaker, lambda c: "x",
                     on_clap=lambda: "Good morning sir.")
    loop.handle_clap()
    assert speaker.said == ["Good morning sir.", "I'll be here, sir."]


# -- the brief ---------------------------------------------------------------------------------------------

def test_brief_greets_by_time_of_day_and_never_waits_on_a_slow_part(monkeypatch):
    from prometheus.voice import brief

    assert brief.greeting(datetime(2026, 11, 11, 8, 5)) == "Good morning sir. It's 8:05 AM."
    assert brief.greeting(datetime(2026, 11, 11, 19, 40)).startswith("Good evening sir. It's 7:40 PM")
    monkeypatch.setattr(brief, "_weather", lambda s: "In Delhi, it's 18 to 27 degrees, clear.")
    monkeypatch.setattr(brief, "_plan", lambda s: "Your day is clear.")
    monkeypatch.setattr(brief, "_messages", lambda s: threading.Event().wait(3) or "never")
    monkeypatch.setattr(brief, "_money_and_study", lambda s: "12 flashcards are due: say quiz me when you're ready.")
    import time

    t = time.monotonic()
    text = brief.build(SimpleNamespace(settings=SimpleNamespace()), datetime(2026, 11, 11, 19, 40), wait=0.5)
    assert time.monotonic() - t < 1.5 and "never" not in text
    assert "Delhi" in text and "flashcards" in text and text.endswith("What would you like to do?")


def test_newest_models_first_older_ones_when_refused():
    tried = []

    def stt(request):
        model = b"scribe_v3" if b"scribe_v3" in request.content else b"scribe_v2"
        tried.append(model)
        return httpx.Response(422) if model == b"scribe_v3" else httpx.Response(200, json={"text": "hello"})

    ear = ElevenHearing("k", http=httpx.Client(transport=httpx.MockTransport(stt)))
    assert ear.text(b"\x00" * 3200) == "hello" and ear.model == "scribe_v2" and tried == [b"scribe_v3", b"scribe_v2"]
    ear.text(b"\x00" * 3200)
    assert tried[-1] == b"scribe_v2"                     # remembered: no second try of the refused one

    FakeOut.played = []

    def tts(request):
        model = json.loads(request.content)["model_id"]
        if model == "eleven_v4_turbo":
            return httpx.Response(404)
        return httpx.Response(200, stream=httpx.ByteStream(b"\x00\x01" * 50))

    mouth = ElevenSpeaker("k", output=FakeOut, http=httpx.Client(transport=httpx.MockTransport(tts)))
    assert mouth.say("Hello.") is True and mouth.model == "eleven_flash_v2_5"
