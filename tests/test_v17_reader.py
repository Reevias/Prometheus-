import threading
import time

from prometheus.quick import understand
from prometheus.voice import reader as rd
from prometheus.voice.reader import Reader, paragraphs

TEXT = "\n\n".join(f"Paragraph {i}. Light travels in straight lines. It reflects off mirrors." for i in range(6))


class Speaker:
    def __init__(self):
        self.said = []
        self.gate = threading.Event()

    def say(self, text, speed=1.0, stop=None):
        self.said.append((text, speed))
        for _ in range(100):                 # "speaking" until stopped or released
            if stop is not None and stop.is_set():
                return False
            if self.gate.wait(0.01):
                return True
        return True

    def stop(self):
        pass


def wait_for(cond, secs=3.0):
    end = time.time() + secs
    while time.time() < end and not cond():
        time.sleep(0.01)
    return cond()


def test_paragraphs_split_long_text():
    parts = paragraphs("One sentence. " * 200)
    assert len(parts) > 3 and all(len(p) <= 700 for p in parts)


def test_read_pause_back_explain_speed_stop(app_client, monkeypatch):
    _, services = app_client()
    spk = Speaker()
    r = Reader(services, speaker=spk)
    monkeypatch.setattr(rd, "CURRENT", r)
    r.load(TEXT, "Optics chapter")
    r.start()
    assert wait_for(lambda: spk.said) and spk.said[0][0].startswith("Paragraph 0")
    assert "Paused" in r.pause() and r.state == "paused" and r.index == 0
    r.resume()
    spk.gate.set()                            # let paragraphs finish
    assert wait_for(lambda: r.index >= 2)
    spk.gate.clear()
    r.move(-1)
    assert wait_for(lambda: spk.said[-1][0].startswith(f"Paragraph {r.index}"))
    assert "1.1" in r.set_speed(True)
    services.router.complete = lambda *a, **k: type("R", (), {"text": "Light goes straight, like a laser pointer."})()
    out = r.explain()
    assert "laser" in out and any("laser" in t for t, _ in spk.said)
    status = r.status()
    assert status["title"] == "Optics chapter" and status["now"].startswith("Paragraph")
    assert "Stopped" in r.stop() and r.state == "off"


def test_voice_words_only_while_reading(monkeypatch, app_client):
    _, services = app_client()
    r = Reader(services, speaker=Speaker())
    monkeypatch.setattr(rd, "CURRENT", r)
    assert understand("pause") is None                                  # nothing is being read
    r.load(TEXT, "x")
    r.state = "reading"
    assert understand("pause").steps[0]["inputs"]["action"] == "pause"
    assert understand("explain that part again").steps[0]["inputs"]["action"] == "explain"
    assert understand("read faster please").steps[0]["inputs"]["action"] == "faster"
    assert understand("stop focus").steps[0]["agent"] == "focus"        # not taken by the reader
    r.state = "off"


def test_panel_and_buttons(app_client, monkeypatch):
    from tests.conftest import login

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    r = Reader(services, speaker=Speaker())
    monkeypatch.setattr(rd, "CURRENT", r)
    r.load(TEXT, "Optics chapter")
    r.state = "paused"
    html = client.get("/reader/panel").text
    assert "Optics chapter" in html and "reader-now" in html and "Continue" in html
    client.post("/reader/stop", headers=h)
    assert r.state == "off"
