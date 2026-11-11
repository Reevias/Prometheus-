import json
import time
from datetime import date

from prometheus.exams import Exams, make_days, parse_date, topics_from_text

TODAY = date(2026, 11, 11)
NOON = time.mktime((2026, 11, 11, 12, 0, 0, 0, 0, -1))


def test_dates_and_topics():
    assert parse_date("20 November", TODAY) == date(2026, 11, 20)
    assert parse_date("5 Jan", TODAY) == date(2027, 1, 5)
    assert parse_date("2027-03-20", TODAY) == date(2027, 3, 20) and parse_date("in 2 weeks", TODAY) == date(2026, 11, 25)
    assert topics_from_text("1. Kinematics\n2. Laws of motion\n3. Work and energy") == ["Kinematics", "Laws of motion",
                                                                                     "Work and energy"]
    assert topics_from_text("optics; waves; thermodynamics") == ["optics", "waves", "thermodynamics"]


def test_spread_with_revision_days():
    days = make_days([f"T{i}" for i in range(8)], TODAY, date(2026, 11, 21))
    assert len(days) == 10 and [k for _, _, k in days].count("revise") == 2
    assert sum(len(t) for _, t, k in days if k == "study") == 8


def test_plan_checkin_and_moving_missed_topics(make_ctx):
    store = make_ctx().store
    now = [NOON]
    ex = Exams(store, clock=lambda: now[0])
    pid = ex.create("Physics", date(2026, 11, 16), ["Kinematics", "Laws of motion", "Work", "Energy", "Gravitation"])
    assert "Kinematics" in ex.today_text() and "5 days left" in ex.today_text()
    q, ids = ex.checkin_question()
    store.kv_set("exam_checkin", json.dumps({"chat": 7, "days": ids, "asked": now[0]}))
    assert ex.answer_checkin(8, "no") is None                              # another chat: not an answer
    out = ex.answer_checkin(7, "only kinematics")
    assert "Moved Laws of motion" in out
    days = ex.days(pid)
    assert days[0]["topics"] == ["Kinematics"] and any("Laws of motion" in d["topics"] for d in days[1:])
    assert ex.answer_checkin(7, "yes") is None                             # asked once
    assert "Physics: 5 day(s) to go" in ex.show(pid)


def test_agent_and_evening_post(app_client):
    _, services = app_client()
    from prometheus.agents import exams_agent
    from prometheus.agents.exams_agent import ExamsAgent

    ctx = services.orchestrator.make_context(services.store.create_task(services.store.create_chat("c"), "x"), 0)
    res = ExamsAgent().run("x", {"action": "plan", "exam": "Maths", "date": "in 10 days",
                                 "topics": "Algebra\nCalculus\nProbability\nVectors"}, ctx)
    assert res.status == "done" and "Algebra" in res.summary and "each day" in res.summary.lower()
    clock = services.briefing
    clock.notify = None
    services.settings.exam_study_time = time.strftime("%H:%M", time.localtime(time.time() - 60))
    services.settings.exam_checkin_time = time.strftime("%H:%M", time.localtime(time.time() - 60))
    assert clock.exam_posts(time.time(), "2026-11-11") == ["exam_post", "exam_ask"]
    chat = clock.study_chat()
    assert "Did you finish" in services.store.messages(chat)[-1].text
    out = services.orchestrator.run(chat, "yes all done")
    assert "done" in out.answer.lower() and exams_agent.EXAMS.for_day() == []
