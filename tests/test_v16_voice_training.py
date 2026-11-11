import threading
import time

from prometheus.voice import text as vt
from prometheus.voice.training import ProfileStore, Trainer, learn, thresholds
from tests.conftest import login

HEARD = ["Hello, permit us.", "Hello Prometheus.", "Hello, permit us.", "Yellow Prometheus!", "Hello, promise.",
         "Hello, permit us.", "Hello, Prometheus.", "Hello pro mitheas."]


def samples(texts=HEARD):
    return [{"text": t, "seconds": 1.4, "level": 900.0, "vosk": "Prometheus" in t} for t in texts]


def test_learns_how_this_voice_is_heard():
    profile = learn(samples())
    assert "permitus" in profile["aliases"] and "promise" not in profile["aliases"]     # common words never
    assert "yellow" in profile["greetings"]
    assert profile["after"] > profile["before"]
    vt.set_learned(profile)
    try:
        assert vt.has_wake_word("Hello permit us, what time is it?")
        assert vt.strip_wake("Hello permit us, what time is it?") == "What time is it?"
        assert not vt.has_wake_word("I permit us to go out")             # only right after a greeting
        assert not vt.has_wake_word("hello, I promise")
        assert vt.has_wake_word("Hello Prometheus")                      # the usual spellings still work
    finally:
        vt.set_learned(None)
    assert not vt.has_wake_word("Hello permit us")


def test_soft_or_slow_voices_get_easier_thresholds():
    t = thresholds({"samples": 8, "level": 800.0, "phrase_seconds": 2.5})
    assert t["loud"] < 500 and t["burst_max"] > 4.5
    assert thresholds(None) == {}


class FakeMic:
    def __init__(self):
        self.reads = 0

    def flush(self):
        pass

    def read(self, timeout=0.5):
        self.reads += 1
        n = self.reads % 100
        return (b"\x10\x27" * 480) if 10 <= n < 50 else (b"\x00\x00" * 480)   # speech, then quiet


class FakeLoop:
    def __init__(self):
        self.mic, self.training = FakeMic(), False
        self.noise = type("N", (), {"level": 100.0})()
        self.spotter = type("S", (), {"loud_min": 500.0, "burst_max": 4.5})()
        self.speaker = type("Sp", (), {"chime": lambda self: None})()
        self.heard = iter(HEARD)
        self.transcriber = type("T", (), {"text": lambda _, audio: next(self.heard)})()

    def _speak_quietly(self, action):
        action()


def test_training_run_saves_and_applies(tmp_path, monkeypatch):
    loop = FakeLoop()
    service = type("V", (), {})()
    service.loop, service.models = loop, type("M", (), {"folder": tmp_path})()
    applied = []
    service.apply_profile = applied.append
    trainer = Trainer(service)
    trainer.pause = 0
    assert trainer.start()
    for _ in range(200):
        if not trainer.running:
            break
        time.sleep(0.02)
    assert not loop.training and "Done" in trainer.progress, trainer.progress
    assert ProfileStore(tmp_path).load()["samples"] == 8 and applied[0]["aliases"] == ["permitus"]


def test_train_buttons(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    r = client.post("/voice/train", headers=h)
    assert r.status_code == 200 and "Turn voice on first" in r.text
    assert "Train wake phrase" in client.get("/voice/train").text
