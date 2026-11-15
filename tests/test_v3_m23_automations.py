"""M2.3 automation builder: once, repeating, and "when something happens" (watcher events), each with ask-first."""
from __future__ import annotations

import datetime as dt
import time
from types import SimpleNamespace

from conftest import login

from prometheus import automations as auto
from prometheus.web.pages_v3 import next_at


def test_next_at_daily_weekdays_weekly():
    fri_9pm = dt.datetime(2026, 11, 13, 21, 0).timestamp()          # a Friday evening
    d = dt.datetime.fromtimestamp(next_at(8, 0, "daily", now=fri_9pm))
    assert (d.weekday(), d.hour, d.minute) == (5, 8, 0)               # Saturday 8:00
    w = dt.datetime.fromtimestamp(next_at(8, 0, "weekdays", now=fri_9pm))
    assert w.weekday() == 0                                             # skips the weekend: Monday
    k = dt.datetime.fromtimestamp(next_at(18, 30, "weekly", weekday=2, now=fri_9pm))
    assert (k.weekday(), k.hour, k.minute) == (2, 18, 30)


def test_builder_makes_timed_automations(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    assert "When does it run?" in client.get("/v3/automations/new").text
    later = (dt.datetime.now() + dt.timedelta(days=2)).replace(second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M")
    r = client.post("/api/v3/automations/build", data={"when": "once", "at": later, "action": "remind",
                                                      "text": "call grandma"}, headers=h)
    assert r.status_code == 200, r.text
    r = client.post("/api/v3/automations/build", data={"when": "repeat", "time_of_day": "07:30", "repeat": "weekdays",
                                                      "action": "do", "text": "summarise my unread email",
                                                      "ask_first": "1"}, headers=h)
    assert r.status_code == 200, r.text
    rows = {a["text"]: a for a in st.automations()}
    assert rows["call grandma"]["kind"] == "reminder" and rows["call grandma"]["repeat"] == ""
    goal = rows["summarise my unread email"]
    assert goal["kind"] == "goal" and goal["repeat"] == "weekdays" and goal["ask_first"] == 1
    assert dt.datetime.fromtimestamp(goal["due"]).strftime("%H:%M") == "07:30"
    past = (dt.datetime.now() - dt.timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M")
    assert client.post("/api/v3/automations/build", data={"when": "once", "at": past, "text": "x"}, headers=h).status_code == 400
    assert client.post("/api/v3/automations/build", data={"when": "once", "at": later, "text": " "}, headers=h).status_code == 400
    page = client.get("/v3/automations").text
    assert "Asks first" in page and "Yes, on Approvals" in page


def test_ask_first_goal_waits_for_your_yes(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    cid = auto.chat_id(st)
    rid = st.add_reminder(cid, "tidy my desktop", time.time() - 5, "daily", "goal")
    st._write("UPDATE reminders SET ask_first = 1 WHERE id = ?", (rid,))
    started = []
    services.start_task = lambda chat, text, read_only=False: started.append((chat, text))
    services.reminders.tick()
    assert started == []                                                   # it asked instead of running
    waiting = [a for a in st.big_approvals() if a.kind == auto.APPROVAL_KIND]
    assert len(waiting) == 1 and "tidy my desktop" in waiting[0].summary
    r = client.post(f"/api/v3/approvals/{waiting[0].id}", data={"decision": "approve"}, headers=h)
    assert r.status_code == 200 and started == [(cid, "tidy my desktop")]
    # switching ask-first off: it runs at its time
    client.post(f"/api/v3/automations/{rid}/ask", data={}, headers=h)
    st._write("UPDATE reminders SET due = ? WHERE id = ?", (time.time() - 5, rid))
    services.reminders.tick()
    assert started[-1] == (cid, "tidy my desktop") and len(started) == 2


def test_new_file_automation_fires_from_the_watcher(app_client, tmp_path):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    inbox = services.home / "Downloads"
    inbox.mkdir(exist_ok=True)
    r = client.post("/api/v3/automations/build", data={"when": "event", "event": "new_file", "folder": str(inbox),
                                                      "action": "do", "text": "file new PDFs into Documents",
                                                      "name": "Tidy PDFs"}, headers=h)
    assert r.status_code == 200, r.text
    assert client.post("/api/v3/automations/build", data={"when": "event", "event": "new_file", "folder": "/etc",
                                                          "text": "x"}, headers=h).status_code == 409   # not allowed
    started = []
    services.start_task = lambda chat, text, read_only=False: started.append(text)
    w = services.watchers
    w.check_folders()                                            # first look: what's there already
    (inbox / "lecture.pdf").write_bytes(b"%PDF")
    w.check_folders()
    asked = [a for a in st.big_approvals() if a.kind == auto.APPROVAL_KIND]
    assert len(asked) == 1 and "Tidy PDFs" in asked[0].summary and started == []      # asks first by default
    assert "lecture.pdf" in asked[0].payload["text"] and "<untrusted" in asked[0].payload["text"]
    rule = auto.rules(st)[0]
    client.post(f"/api/v3/automations/rules/{rule['id']}/ask", data={}, headers=h)    # now: just run
    (inbox / "notes.pdf").write_bytes(b"%PDF")
    w.check_folders()
    assert len(started) == 1 and "notes.pdf" in started[0]
    page = client.get("/v3/automations").text
    assert "Tidy PDFs" in page and "A new file appears in a folder" in page and "Started it" in page
    client.post(f"/api/v3/automations/rules/{rule['id']}/switch", data={}, headers=h)   # paused: nothing happens
    (inbox / "more.pdf").write_bytes(b"%PDF")
    w.check_folders()
    assert len(started) == 1
    assert client.post(f"/api/v3/automations/rules/{rule['id']}/delete", headers=h).json()["ok"]
    assert auto.rules(st) == []


def test_battery_event_runs_once_until_it_clears(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    client.post("/api/v3/automations/build", data={"when": "event", "event": "battery", "action": "remind",
                                                  "text": "plug in the charger"}, headers=h)
    w = services.watchers
    level = {"percent": 15, "charging": False}
    w.laptop = SimpleNamespace(battery=lambda: dict(level), cpu_load=lambda: 10)
    w.check_battery()
    w.check_battery()                                            # still low: not again
    msgs = [m.text for m in st.messages(auto.chat_id(st)) if "plug in the charger" in m.text]
    assert len(msgs) == 1
    level.update(percent=60, charging=True)
    w.check_battery()                                            # cleared
    level.update(percent=12, charging=False)
    w.check_battery()                                            # low again: runs again
    assert len([m for m in st.messages(auto.chat_id(st)) if "plug in the charger" in m.text]) == 2


def test_rule_run_now_and_edit(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    st = services.store
    rid = auto.add_rule(st, "Disk", "disk", "remind", "clear some space")
    assert "Reminded" in client.post(f"/api/v3/automations/rules/{rid}/run", headers=h).json()["message"]
    assert client.post(f"/api/v3/automations/rules/{rid}/edit", data={"text": "empty the Recycle Bin"}, headers=h).json()["ok"]
    assert auto.rule(st, rid)["text"] == "empty the Recycle Bin"
    assert client.post("/api/v3/automations/rules/999/run", headers=h).status_code == 404
