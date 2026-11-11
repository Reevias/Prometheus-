import time
from datetime import datetime

import pytest

from prometheus.assignments import Assignments, clean_title, parse_due
from prometheus.quick import understand

NOW = time.mktime((2026, 11, 11, 12, 0, 0, 0, 0, -1))            # a Wednesday


@pytest.mark.parametrize("text, expect", [
    ("due Friday", (2026, 11, 13, 23, 59)), ("by 15 Nov 5pm", (2026, 11, 15, 17, 0)),
    ("due 20/11 at 11:59 pm", (2026, 11, 20, 23, 59)), ("deadline tomorrow", (2026, 11, 12, 23, 59)),
    ("submit by November 30, 2026", (2026, 11, 30, 23, 59)), ("due next Monday", (2026, 11, 16, 23, 59))])
def test_dates(text, expect):
    d = datetime.fromtimestamp(parse_due(text, NOW))
    assert (d.year, d.month, d.day, d.hour, d.minute) == expect


def test_told_and_reminders(make_ctx):
    store = make_ctx().store
    tr = Assignments(store, clock=lambda: NOW)
    out = tr.tell("physics lab report due Saturday 5pm")
    assert "Physics lab report" in out and "Sat 14 Nov, 5:00 PM" in out
    texts = sorted(r["text"] for r in store.reminders())
    assert texts == ["Physics lab report is due in two days (Sat 14 Nov, 5:00 PM)",
                     "Physics lab report is due today (Sat 14 Nov, 5:00 PM)"]
    assert "already" in tr.tell("physics lab report due Saturday 5pm")
    assert "Physics lab report" in tr.soon_line(4)
    assert "done" in tr.mark_done("physics lab").lower() and not store.reminders()


class Box:
    def search(self, sender="", days=0, limit=10, **kw):
        if sender != "@college.edu":
            return []
        return [{"id": "9", "from": "prof@college.edu", "subject": "New assignment: Thermodynamics problem set",
                 "snippet": "Please submit by 18 Nov 11:59 pm on the portal."},
                {"id": "10", "from": "prof@college.edu", "subject": "Class cancelled", "snippet": "No class today."}]


def test_email_scan(make_ctx):
    store = make_ctx().store
    tr = Assignments(store, clock=lambda: NOW)
    added = tr.scan_email(Box(), ["@college.edu"])
    assert len(added) == 1 and tr.upcoming()[0]["title"] == "Thermodynamics problem set"
    assert tr.scan_email(Box(), ["@college.edu"]) == []                     # each email once
    assert clean_title("Re: Assignment posted: Essay 2 - due Friday") == "Essay 2"


def test_phrases():
    step = understand("my maths assignment is due Friday").steps[0]
    assert step["agent"] == "assignments" and step["inputs"]["title"] == "maths assignment"
    assert understand("what's due this week").steps[0]["inputs"]["action"] == "list"
    assert understand("the meeting is due Friday") is None or understand("the meeting is due Friday").steps[0]["agent"] != "assignments"
