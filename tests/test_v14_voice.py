import io
import math
import subprocess
import zipfile
from array import array

import pytest

from prometheus.voice.audio import FRAME, RATE, Endpointer
from prometheus.voice.engines import VoiceModels, VoiceNotReady, _get, _safe_unzip
from prometheus.voice.loop import VoiceLoop
from prometheus.voice.text import has_wake_word, speakable, strip_wake
from tests.conftest import login


def tone(seconds, loud=True):
    n = int(seconds * RATE / FRAME)
    amp = 6000 if loud else 0
    frame = array("h", (int(amp * math.sin(i / 3)) for i in range(FRAME))).tobytes()
    return [frame] * n


def test_wake_word_and_command_text():
    assert has_wake_word("Hey Prometheus, what time is it?") and has_wake_word("Promethius play music")
    for heard in ("Permitheus", "Promethece.", "Prumi Theus is a f-", "Promethius play"):
        assert has_wake_word(heard), heard
    for talk in ("It was quite promising", "the problem with the process", "they promoted the theory",
                 "prometric test", "a promise", "Prom theme party"):
        assert not has_wake_word(talk), talk
    assert strip_wake("Permitheus, open YouTube") == "Open YouTube"
    assert strip_wake("Hello Prometheus. Open Spotify.") == "Open Spotify."
    assert has_wake_word("Hello, Prometheus!")
    assert strip_wake("Hey Prometheus, open YouTube.") == "Open YouTube."
    assert strip_wake("Prometheus.") == ""
    assert strip_wake("prometheus what's the weather") == "What's the weather"


def test_speakable_is_short_and_plain():
    answer = ("## Weather\n- **Light rain** after 4 pm [1].\nIt will be 29°C. See https://x.y/z\n"
              "| a | b |\n```code```\n" + "More detail here. " * 40)
    spoken = speakable(answer)
    assert "**" not in spoken and "http" not in spoken and "|" not in spoken and "[1]" not in spoken
    assert spoken.startswith("Weather. Light rain after 4 pm. It will be 29°C.") and spoken.endswith("The full answer is on screen.")
    assert len(spoken) < 400
    assert speakable("Paris.") == "Paris."


def test_endpointer_waits_for_a_pause_or_gives_up():
    ep = Endpointer(pause=0.6)
    verdicts = [ep.feed(f) for f in tone(1.0) + tone(1.0, loud=False)]
    assert "done" in verdicts and verdicts.index("done") < len(verdicts)
    quiet = Endpointer(wait_seconds=1.0)
    assert [quiet.feed(f) for f in tone(1.2, loud=False)][-1] == "nothing"
    long = Endpointer(max_seconds=1.0)
    assert "done" in [long.feed(f) for f in tone(1.5)]


class FakeMic:
    def __init__(self, frames):
        self.frames = list(frames)
        self.flushed = 0

    def start(self):
        pass

    def stop(self):
        pass

    def read(self, timeout=0.5):
        return self.frames.pop(0) if self.frames else None

    def flush(self):
        self.flushed += 1


class FakeSpotter:
    def __init__(self, at):
        self.at, self.n = at, 0

    def feed(self, frame):
        self.n += 1
        return b"wake" if self.n == self.at else None

    def reset(self):
        pass


class FakeSpeaker:
    def __init__(self):
        self.said, self.chimes = [], 0

    def say(self, text):
        self.said.append(text)

    def chime(self):
        self.chimes += 1

    def ask(self):          # "Yes sir?": heard the name, waiting for the command
        self.chimes += 1

    def ack(self):          # "Yes sir.": heard the command
        self.acks = getattr(self, "acks", 0) + 1


class FakeTranscriber:
    def __init__(self, texts):
        self.texts = list(texts)

    def text(self, audio):
        return self.texts.pop(0) if self.texts else ""


def run_loop(frames, texts, answer="It is 4 pm.", at=5):
    mic, speaker, asked = FakeMic(frames), FakeSpeaker(), []
    loop = VoiceLoop(mic, FakeSpotter(at), FakeTranscriber(texts), speaker,
                     lambda cmd: (asked.append(cmd), answer)[1])
    for _ in range(len(frames) + 5):
        frame = mic.read()
        if frame is None:
            break
        wake = loop.spotter.feed(frame)
        if wake is not None:
            loop.handle_wake(wake)
    return loop, asked, speaker, mic


def test_command_in_one_breath_is_run_and_answer_spoken():
    loop, asked, speaker, mic = run_loop(tone(0.5) + tone(1.0) + tone(1.2, loud=False),
                                         ["Prometheus, what time is it?"])
    assert asked == ["What time is it?"] and speaker.said == ["It is 4 pm."] and speaker.chimes == 0
    assert mic.flushed >= 1 and loop.last_heard == "What time is it?"


def test_wake_word_alone_chimes_then_listens():
    loop, asked, speaker, _ = run_loop(tone(0.5) + tone(1.2, loud=False) + tone(1.0) + tone(1.2, loud=False),
                                       ["Prometheus.", "open YouTube"])
    assert speaker.chimes == 1 and asked == ["Open YouTube"]
    assert speaker.said[0] == "Yes sir. Open YouTube."        # an action is said back before it's done


class HeardSpotter(FakeSpotter):
    """A spotter whose Whisper check already heard the whole phrase."""

    def __init__(self, at, text, done=True):
        super().__init__(at)
        self.last_text, self.phrase_done = text, done


def run_with(spotter, frames, texts=()):
    mic, speaker, asked = FakeMic(frames), FakeSpeaker(), []
    transcriber = FakeTranscriber(list(texts))
    loop = VoiceLoop(mic, spotter, transcriber, speaker, lambda c: (asked.append(c), "Done.")[1])
    while (frame := mic.read()) is not None:
        wake = spotter.feed(frame)
        if wake is not None:
            loop.handle_wake(wake)
    return asked, speaker, transcriber


def test_command_in_the_same_breath_is_used_at_once_and_acknowledged():
    asked, speaker, transcriber = run_with(HeardSpotter(3, "Hello Prometheus, open YouTube."), tone(0.5),
                                           ["should not be asked"])
    assert asked == ["Open YouTube."] and speaker.said[0] == "Yes sir. Open YouTube." and speaker.chimes == 0
    assert transcriber.texts == ["should not be asked"]          # no second transcription: faster


def test_name_alone_says_yes_sir_then_listens():
    asked, speaker, _ = run_with(HeardSpotter(3, "Hello Prometheus."), tone(0.3) + tone(1.0) + tone(1.2, loud=False),
                                 ["what's the weather"])
    assert speaker.chimes == 1 and asked == ["What's the weather"] and speaker.acks == 1


def test_every_new_command_needs_the_wake_phrase():
    # after one command is answered, more talking without "Hello Prometheus" is ignored
    spotter = HeardSpotter(3, "Hello Prometheus, what time is it?")
    asked, _, _ = run_with(spotter, tone(0.5) + tone(2.0) + tone(1.0, loud=False))
    assert asked == ["What time is it?"]


def test_muted_loop_ignores_everything():
    mic = FakeMic(tone(1.0))
    spotter = FakeSpotter(1)
    loop = VoiceLoop(mic, spotter, FakeTranscriber(["Prometheus hi"]), FakeSpeaker(), lambda c: "x")
    loop.set_muted(True)
    assert loop.state == "muted"
    import threading
    t = threading.Thread(target=loop.run, daemon=True)
    t.start()
    import time
    time.sleep(0.3)
    loop._stop.set()
    t.join(2)
    assert spotter.n == 0


def test_downloads_only_from_known_hosts_and_unzip_is_safe(tmp_path):
    with pytest.raises(VoiceNotReady):
        _get(None, "https://evil.example.com/model.zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../../escape.txt", "x")
    with pytest.raises(VoiceNotReady):
        _safe_unzip(buf.getvalue(), tmp_path / "voice")
    assert not (tmp_path / "escape.txt").exists()
    assert len(VoiceModels(tmp_path / "voice").missing()) == 3


def test_voice_command_goes_to_the_voice_chat(app_client):
    import json

    from prometheus.models.fake import FakeProvider

    reply = json.dumps({"answer": "Paris.", "topic": "geography", "title": "x", "remember": [], "steps": []})
    client, services = app_client(providers=[FakeProvider([reply], name="fake")])
    spoken = services.voice.command("What is the capital of France?")
    assert spoken == "Paris."
    chat_id = services.voice.voice_chat()
    title = services.store.chat_title(chat_id)
    assert title.startswith("Voice ") and services.store.chat_named_by(chat_id) == "user"
    assert [m.text for m in services.store.messages(chat_id)] == ["What is the capital of France?", "Paris."]


def test_voice_settings_and_pill(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    page = client.get("/settings").text
    assert "Voice" in page and "Listen for" in page
    assert client.get("/voice/pill").status_code == 200
    r = client.post("/settings/voice", data={"on": "1"}, headers=h)
    assert r.status_code == 200 and services.settings.voice_enabled
    assert services.voice.loop is None and services.voice.error      # nothing to listen with here, said plainly
    client.post("/settings/voice", data={}, headers=h)
    assert not services.settings.voice_enabled
    import os
    if os.name != "nt":
        r = client.post("/settings/voice/autostart", data={"on": "1"}, headers=h)
        assert r.status_code == 400 and "Windows" in r.text


def test_autostart_shortcut_runs_background_mode(tmp_path, monkeypatch):
    from prometheus import launcher

    monkeypatch.setenv("APPDATA", str(tmp_path))
    seen = []

    def run(argv, env=None, **kw):
        seen.append(env)
        return subprocess.CompletedProcess(argv, 0, "", "")

    assert "start with Windows" in launcher.set_autostart(True, run, windows=True)
    assert seen[0]["PROM_ARGS"] == "-m prometheus.cli background"
    assert seen[0]["PROM_LINK"].endswith("Startup/Prometheus (background).lnk".replace("/", os.sep)) \
        if (os := __import__("os")) else True
    launcher.startup_link().parent.mkdir(parents=True, exist_ok=True)
    launcher.startup_link().write_text("x")
    launcher.set_autostart(False, run, windows=True)
    assert not launcher.startup_link().exists()


def test_window_joins_a_running_background_copy():
    from prometheus.desktop import AttachedServer, running_copy

    assert running_copy(9) is False                       # nothing on that port
    s = AttachedServer(8000)
    assert s.start() == 8000 and s.stop() is None


def test_healthz_is_open_and_tiny(app_client):
    client, _ = app_client()
    assert client.get("/healthz").json() == {"app": "prometheus"}


def test_questions_get_a_plain_yes_sir_and_timing_is_recorded():
    asked, speaker, _ = run_with(HeardSpotter(3, "Hello Prometheus, what time is it?"), tone(0.5), [])
    assert asked == ["What time is it?"] and speaker.acks == 1 and not any(s.startswith("Yes sir.") for s in speaker.said)


def test_the_chosen_speech_model_hears_the_command_again():
    class Better(FakeTranscriber):
        command = True

    loop = VoiceLoop(FakeMic(tone(0.5)), HeardSpotter(3, "Hello Prometheus, open you tube."), Better(
        ["Hello Prometheus, open YouTube."]), FakeSpeaker(), lambda c: asked.append(c) or "")
    asked = []
    loop.handle_wake(b"\x00" * 960)
    assert asked == ["Open YouTube."] and set(loop.last_timing) == {"hearing", "thinking", "speaking"}


def test_quiz_answers_need_no_wake_phrase():
    asked = []
    quiz = {"left": 2}

    def command(c):
        asked.append(c)
        if c.startswith("Quiz"):
            return "Q1. Symbol for sodium?"
        quiz["left"] -= 1
        return "✓ Right." if quiz["left"] else "Quiz done."

    mic = FakeMic(tone(1.0) + tone(1.0, loud=False) + tone(1.0) + tone(1.0, loud=False))
    loop = VoiceLoop(mic, HeardSpotter(3, "Hello Prometheus, quiz me"), FakeTranscriber(["Na", "Cl"]), FakeSpeaker(),
                     command)
    loop.followup = lambda: quiz["left"] > 0 and len(asked) >= 1
    loop.handle_wake(b"\x00" * 960)
    assert asked == ["Quiz me", "Na", "Cl"]


def test_its_own_voice_coming_back_is_ignored():
    asked = []
    loop = VoiceLoop(FakeMic(tone(0.5)), HeardSpotter(3, "Hello Prometheus, the weather in Delhi is 31 degrees"),
                     FakeTranscriber([]), FakeSpeaker(), lambda c: asked.append(c) or "")
    loop.last_said, loop.last_said_at = "The weather in Delhi is 31 degrees and sunny.", __import__("time").monotonic()
    loop.handle_wake(b"\x00" * 960)
    assert asked == []
    assert not loop.is_own_echo("open YouTube")


def test_unclear_words_are_asked_again():
    class Unsure(FakeTranscriber):
        command = True
        last_confidence, last_no_speech = -1.6, 0.2

    asked = []
    speaker = FakeSpeaker()
    loop = VoiceLoop(FakeMic(tone(0.5) + tone(1.0) + tone(1.2, loud=False)),
                     HeardSpotter(3, "Hello Prometheus, wether gith hub"), Unsure(["Hello Prometheus, wether gith hub",
                                                                                   "what's the weather"]),
                     speaker, lambda c: asked.append(c) or "")
    loop.handle_wake(b"\x00" * 960)
    assert asked == [] and speaker.chimes == 1 and "didn't catch" in speaker.said[-1]
