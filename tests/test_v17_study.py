import json
import time

from prometheus.models.fake import FakeProvider
from prometheus.study import DAY, Card, Study, schedule

CARDS = {"cards": [{"question": "What does photosynthesis make?", "answer": "glucose and oxygen"},
                   {"question": "Where does it happen?", "answer": "in the chloroplasts"},
                   {"question": "What pigment traps light?", "answer": "chlorophyll"}]}


class Router:
    def __init__(self):
        self.purposes = []

    def complete(self, purpose, messages, system, json_schema=None, max_tokens=0):
        self.purposes.append(purpose)
        if "flashcards" in messages[0]["content"]:
            return FakeProvider([json.dumps(CARDS)]).complete(messages, system)
        return FakeProvider([json.dumps({"verdict": "partly", "feedback": "Partly: glucose and oxygen."})]).complete(
            messages, system)


def test_spaced_schedule():
    c = Card(1, "d", "q", "a", "", 2.5, 0, 0, 0, 0)
    ease, interval, due, reps, _ = schedule(c, "right", 0)
    assert interval == 1 and reps == 1
    c2 = Card(1, "d", "q", "a", "", ease, interval, due, reps, 0)
    assert schedule(c2, "right", 0)[1] == 3
    assert schedule(c2, "wrong", 0)[1] == 1 and schedule(c2, "wrong", 0)[4] == 1


def test_make_quiz_mark_and_requeue(make_ctx, tmp_path):
    store = make_ctx().store
    router = Router()
    now = [1_800_000_000.0]
    study = Study(store, router, settings=None, clock=lambda: now[0])
    assert len(study.make("Photosynthesis makes glucose…", "Biology ch 2", "bio.pdf")) == 3
    assert router.purposes == ["private"]                       # the notes stay on the laptop by default
    chat = store.create_chat("Study")
    q = study.start_quiz(chat, "biology ch 2")
    assert "Q1. What does photosynthesis make?" in q
    out = study.answer(chat, "Glucose and oxygen")
    assert out.startswith("✓") and "Q2." in out
    out = study.answer(chat, "I don't know")                     # wrong: comes back in this quiz
    assert out.startswith("✗") and "chloroplasts" in out
    out = study.answer(chat, "chlorophyll")
    assert "Where does it happen?" in out                        # the missed card again
    out = study.answer(chat, "in chloroplasts")
    assert "Quiz done" in out and study.quiz_state(chat) is None
    assert study.answer(chat, "anything") is None                # no quiz: normal chat again
    path = study.export_anki("Biology ch 2", tmp_path)
    lines = path.read_text().splitlines()
    assert lines[0] == "#separator:tab" and "chlorophyll" in lines[-1]
    now[0] += 2 * DAY
    assert study.due("biology ch 2")                             # the missed card is due again


def test_stop_and_agent_and_orchestrator(app_client):
    _, services = app_client()
    from prometheus.agents import study_agent

    study = study_agent.STUDY
    study.add("chemistry", "Symbol for sodium?", "Na")
    chat = services.store.create_chat("t")
    out = services.orchestrator.run(chat, "quiz me on chemistry")
    assert "Symbol for sodium?" in out.answer
    out = services.orchestrator.run(chat, "Na")                  # answered without the AI planner
    assert "Quiz done: 1 of 1" in out.answer and "(in Outputs)" in out.answer
    pdf = next(study.folder.glob("Quiz chemistry *.pdf"))
    assert pdf.read_bytes()[:4] == b"%PDF"
    assert any(o["path"] == str(pdf) for o in services.store.outputs(chat))


def test_daily_nudge(app_client):
    _, services = app_client()
    from prometheus.agents import study_agent

    study_agent.STUDY.add("history", "Year of independence?", "1947")
    clock = services.briefing
    services.settings.study_reminder = time.strftime("%H:%M", time.localtime(time.time() - 60))
    services.settings.briefing_morning_on = services.settings.briefing_evening_on = False
    clock.notify = None
    assert "study" in clock.tick()
    assert clock.tick() == []                                     # once a day
