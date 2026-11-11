import time

import pytest

from prometheus import quick
from prometheus.quick import is_question, now_line, understand

NOON = time.mktime((2026, 11, 11, 14, 5, 0, 0, 0, -1))


@pytest.mark.parametrize("said", ["What time is it?", "Hello Prometheus, what's the time", "time please",
                                  "Can you tell me the time?", "kitne baje hain", "time kya hai", "What is the time now"])
def test_time(said):
    q = understand(said, now=NOON)
    assert q and q.answer == "It's 2:05 PM."


@pytest.mark.parametrize("said", ["What's the date today?", "what day is it", "aaj kya date hai", "Today's date"])
def test_date(said):
    assert understand(said, now=NOON).answer == "Today is Wednesday, 11 November 2026."


@pytest.mark.parametrize("said, target", [("Open YouTube", "YouTube"), ("open spotify app", "spotify"),
                                          ("Launch Notepad.", "Notepad"), ("youtube kholo", "youtube"),
                                          ("please open whatsapp", "whatsapp")])
def test_open(said, target):
    q = understand(said, now=NOON)
    assert q.steps[0]["agent"] == "system" and q.steps[0]["inputs"] == {"action": "app_open", "target": target}
    assert q.action


@pytest.mark.parametrize("said", ["open my physics notes file", "open the Downloads folder", "open C:/x.pdf"])
def test_files_go_to_the_ai(said):
    assert understand(said, now=NOON) is None


@pytest.mark.parametrize("said, text, when", [
    ("Set a timer for 10 minutes", "Timer", "in 10 minutes"), ("timer 5 min", "Timer", "in 5 min"),
    ("alarm at 6am", "Alarm", "6am"), ("remind me to call mom at 6pm", "call mom", "at 6pm"),
    ("remind me in 20 minutes to drink water", "drink water", "in 20 minutes")])
def test_timers_and_reminders(said, text, when):
    step = understand(said, now=NOON).steps[0]
    assert step["agent"] == "reminders" and step["inputs"]["text"] == text and step["inputs"]["when"] == when


def test_volume_battery_weather(make_ctx, monkeypatch):
    store = make_ctx().store
    assert understand("volume 30", store, NOON).steps[0]["inputs"]["level"] == 30
    assert understand("volume up", store, NOON).steps[0]["inputs"]["level"] == 50
    assert understand("turn down the volume", store, NOON).steps[0]["inputs"]["level"] == 30
    assert understand("mute", store, NOON).steps[0]["inputs"] == {"action": "mute"}
    assert understand("how much battery is left?", store, NOON).steps[0]["inputs"] == {"action": "battery"}
    monkeypatch.setattr(quick, "WEATHER", lambda day: "Delhi: 24–31°C, clear" if day == 0 else "Delhi: rain")
    assert understand("what's the weather", store, NOON).answer == "Today: Delhi: 24–31°C, clear."
    assert understand("will it rain tomorrow", store, NOON).answer == "Tomorrow: Delhi: rain."
    assert understand("weather in Tokyo", store, NOON) is None                 # another place: the AI looks it up


@pytest.mark.parametrize("said", ["Explain photosynthesis", "what time does the train leave", "write an essay",
                                  "summarise my emails", "what is the time complexity of quicksort"])
def test_everything_else_goes_to_the_ai(said):
    q = understand(said, now=NOON)
    assert q is None or not q.answer.startswith("It's")


def test_questions_and_now_line():
    assert is_question("what's the weather") and is_question("kya baje hain") and not is_question("open youtube")
    assert "Wednesday, 11 November 2026, 2:05 PM" in now_line(NOON)


def test_orchestrator_answers_instantly_without_ai(app_client):
    _, services = app_client()
    orch = services.orchestrator
    calls = []
    orch._plan = lambda *a, **k: calls.append(a) or (_ for _ in ()).throw(AssertionError("planner called"))
    chat = services.store.create_chat("t")
    out = orch.run(chat, "Hello Prometheus, what time is it?")
    assert out.status == "done" and out.answer.startswith("It's") and not calls
    assert services.store.messages(chat)[-1].model == "instant"
