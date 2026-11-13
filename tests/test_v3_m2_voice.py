"""M2.1 voice in the new interface: the mic button, the global mute, and the live level for the waveform/sphere."""
from __future__ import annotations

import threading
import time

from conftest import login
from test_v14_voice import FakeMic, FakeSpeaker, FakeTranscriber, tone

from prometheus.voice.loop import VoiceLoop


class StaleSpotter:
    """The spotter still holds an old command from an earlier wake: the mic button must never reuse it."""
    last_text = "Hello Prometheus, delete my downloads"
    phrase_done = True

    def feed(self, frame):
        return None

    def reset(self):
        pass


def test_mic_button_asks_then_runs_the_new_command_only():
    asked = []
    mic = FakeMic(tone(1.0) + tone(1.2, loud=False))
    loop = VoiceLoop(mic, StaleSpotter(), FakeTranscriber(["What's the weather?"]), FakeSpeaker(),
                     lambda c: (asked.append(c), "Sunny.")[1])
    assert loop.listen_now()
    loop.handle_button()
    assert asked == ["What's the weather?"]                       # not the stale "delete my downloads"
    assert loop.speaker.chimes == 1 and loop.speaker.said == ["Sunny."]


def test_mic_button_refused_while_muted():
    loop = VoiceLoop(FakeMic([]), StaleSpotter(), FakeTranscriber([]), FakeSpeaker(), lambda c: "")
    loop.set_muted(True)
    assert loop.listen_now() is False


def test_level_follows_the_microphone():
    frames = tone(0.6)
    mic = FakeMic(frames)
    loop = VoiceLoop(mic, StaleSpotter(), FakeTranscriber([]), FakeSpeaker(), lambda c: "")
    seen = []
    t = threading.Thread(target=loop.run, daemon=True)
    t.start()
    for _ in range(40):
        seen.append(loop.level)
        time.sleep(0.01)
    loop._stop.set()
    t.join(2)
    assert max(seen) > 0.05 and all(0 <= v <= 1 for v in seen)


def test_voice_routes_without_voice_set_up(app_client):
    client, services = app_client()
    csrf = login(client)
    services.voice = None
    h = {"X-CSRF-Token": csrf}
    assert client.get("/api/v3/voice").json() == {"available": False}
    r = client.post("/api/v3/voice/listen", headers=h)
    assert r.status_code == 409 and "Voice isn't set up" in r.json()["error"]
    assert client.post("/api/v3/voice/mute", headers=h).status_code == 409
    assert 'id="mic"' not in client.get("/").text                 # no mic button when there's no voice


def test_voice_routes_with_a_running_loop(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    loop = VoiceLoop(FakeMic([]), StaleSpotter(), FakeTranscriber([]), FakeSpeaker(), lambda c: "")
    if services.voice is None:
        raise AssertionError("the test services have no voice object")
    services.voice.loop = loop
    try:
        assert client.post("/api/v3/voice/listen", headers=h).json() == {"listening": True}
        assert loop._listen_now.is_set()
        assert client.post("/api/v3/voice/mute", headers=h).json() == {"muted": True}
        assert client.get("/api/v3/state").json()["voice"]["muted"] is True
        r = client.post("/api/v3/voice/listen", headers=h)
        assert r.status_code == 409 and "muted" in r.json()["error"]
        assert 'id="mic"' in client.get("/").text and 'id="mute"' in client.get("/").text
    finally:
        services.voice.loop = None
