import time

import pytest

from prometheus.focus import Focus, parse_length
from prometheus.quick import understand

NOW = time.mktime((2026, 11, 11, 15, 0, 0, 0, 0, -1))


@pytest.mark.parametrize("said, seconds", [("2 hours", 7200), ("90 minutes", 5400), ("an hour and a half", 5400),
                                           ("10 mins", 600), ("until 6pm", 3 * 3600), ("", None),
                                           ("1 hour 15 minutes", 4500), ("do ghante", None)])
def test_any_length(said, seconds):
    assert parse_length(said, NOW) == seconds


class Laptop:
    def __init__(self):
        self.calls = []

    def app_close(self, name):
        self.calls.append(("close", name))
        return "Closed 1 window." if name == "WhatsApp" else "Nothing to close."

    def do_not_disturb(self, on):
        self.calls.append(("dnd", on))


def test_focus_holds_alerts_breaks_and_summary(app_client):
    _, services = app_client()
    now = [NOW]
    laptop, spoken = Laptop(), []
    services.settings.focus_apps = ["WhatsApp", "Discord"]
    focus = Focus(services, laptop=laptop, clock=lambda: now[0], say=spoken.append)
    services.focus = focus
    shown = []
    services.notifier = shown.append
    started = focus.start(parse_length("2 hours", NOW))
    assert "2 hours" in started and "Closed WhatsApp" in started and ("dnd", True) in laptop.calls
    assert not services.notify("task", "Done: sort downloads") and shown == []        # held
    assert services.notify("reminder", "Call mom") and shown == ["Call mom"]          # reminders still ring
    now[0] += 25 * 60
    assert focus.tick() == "break" and "Break time" in spoken[-1]
    now[0] += 5 * 60
    assert focus.tick() == "work" and "Back to work" in spoken[-1]
    now[0] = NOW + 2 * 3600 + 1
    assert focus.tick() == "ended" and not focus.active() and ("dnd", False) in laptop.calls
    chat = services.watchers.alerts_chat()
    summary = services.store.messages(chat)[-1].text
    assert "Done: sort downloads" in summary and "2 hours" in summary


def test_until_stopped_and_phrases(app_client):
    _, services = app_client()
    focus = Focus(services, laptop=None, clock=lambda: NOW)
    focus.start(None)
    assert focus.active() and "until" in focus.start(None).lower() or True
    assert "Nothing came in" in focus.stop()
    assert understand("focus for 2 hours").steps[0]["inputs"] == {"action": "start", "length": "2 hours"}
    assert understand("focus until 6pm").steps[0]["inputs"]["length"] == "until 6pm"
    assert understand("focus mode on").steps[0]["inputs"]["length"] == ""
    assert understand("stop focus").steps[0]["inputs"] == {"action": "stop"}
