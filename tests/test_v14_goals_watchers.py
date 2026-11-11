import time
from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace

from prometheus.agents.reminders_agent import Reminders
from prometheus.reminders import ReminderClock
from prometheus.watchers import ALERTS_CHAT, Watchers
import prometheus.watchers as watchers_mod
from tests.conftest import login


def yes(ctx):
    import prometheus.agents.reminders_agent as ra

    ra.STORE = ctx.store
    ctx.approvals.register("standing_goal", ra.goal_handler)
    ask = [a for a in ctx.store.asks_for_chat(ctx.chat_id()) if a.kind == "standing_goal"][-1]
    return ctx.approvals.resolve(ask.id, approved=True)


def test_standing_goal_runs_the_task_on_schedule(make_ctx):
    ctx = make_ctx()
    res = Reminders().run("x", {"action": "goal", "text": "sort my Downloads folder", "when": "every night at 11pm"},
                          ctx)
    assert res.status == "waiting_approval" and not ctx.store.reminders()        # asked in the chat first
    assert "Standing goal saved" in yes(ctx)
    goal = ctx.store.reminders()[0]
    assert goal["kind"] == "goal" and goal["repeat"] == "daily"
    started = []
    services = SimpleNamespace(store=ctx.store, voice=None, running={},
                               start_task=lambda chat, text: started.append((chat, text)))
    clock = ReminderClock(services, clock=lambda: goal["due"] + 1)
    assert clock.tick() == 1
    assert started == [(goal["chat_id"], "sort my Downloads folder")]
    assert ctx.store.reminders()[0]["due"] == goal["due"] + 86400                  # again tomorrow night
    assert "Running your standing goal" in ctx.store.messages(goal["chat_id"])[-1].text


def test_goal_waits_if_that_chat_is_busy(make_ctx):
    ctx = make_ctx()
    Reminders().run("x", {"action": "goal", "text": "tidy", "when": "in 1 minute"}, ctx)
    yes(ctx)
    goal = ctx.store.reminders()[0]
    services = SimpleNamespace(store=ctx.store, voice=None, running={goal["chat_id"]: True},
                               start_task=lambda c, t: (_ for _ in ()).throw(AssertionError("must wait")))
    ReminderClock(services, clock=lambda: goal["due"] + 1).tick()
    assert ctx.store.reminders()[0]["due"] > goal["due"] + 200                     # tried again in 5 minutes


class Lap:
    def __init__(self, percent=50, charging=False, cpu=10):
        self.percent, self.charging, self.cpu = percent, charging, cpu

    def battery(self):
        return {"percent": self.percent, "charging": self.charging}

    def cpu_load(self):
        return self.cpu


def services_for(make_ctx, **settings):
    ctx = make_ctx()
    s = SimpleNamespace(watch_battery=True, watch_disk=True, watch_cpu=False, watch_folders=[])
    s.__dict__.update(settings)
    started = []
    svc = SimpleNamespace(store=ctx.store, voice=None, running={}, settings=s, home=ctx.home, policy=ctx.policy,
                          start_task=lambda chat, text: started.append(text))
    return svc, started


def alerts(svc):
    chat = dict((t, c) for c, t in svc.store.chats()).get(ALERTS_CHAT)
    return [m.text for m in svc.store.messages(chat)] if chat else []


def test_battery_alert_once_until_charged(make_ctx):
    svc, _ = services_for(make_ctx, watch_disk=False)
    lap, told = Lap(percent=15), []
    w = Watchers(svc, laptop=lap, notify=told.append)
    w.tick()
    w.tick()
    assert alerts(svc) == ["Battery is at 15%. Plug in the charger soon."] and len(told) == 1
    lap.percent, lap.charging = 60, True
    w.tick()
    lap.percent, lap.charging = 12, False
    w.tick()
    assert len(alerts(svc)) == 2                                                   # said again after charging


def test_disk_and_cpu(make_ctx, monkeypatch):
    svc, _ = services_for(make_ctx, watch_battery=False, watch_cpu=True)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(watchers_mod.shutil, "disk_usage", lambda p: Usage(100 * 1024 ** 3, 95 * 1024 ** 3, 5 * 1024 ** 3))
    lap = Lap(cpu=99)
    w = Watchers(svc, laptop=lap)
    for _ in range(5):
        w.tick()
    said = alerts(svc)
    assert any("95% full" in t for t in said) and any("CPU has been over 90%" in t for t in said)


def test_new_files_in_a_watched_folder(make_ctx, tmp_home):
    downloads = tmp_home.home / "Downloads"
    downloads.mkdir()
    (downloads / "old.pdf").write_text("x")
    svc, started = services_for(make_ctx, watch_battery=False, watch_disk=False,
                                watch_folders=[{"path": str(downloads), "goal": ""}])
    w = Watchers(svc)
    w.tick()                                        # first look: what's there already is not "new"
    assert alerts(svc) == []
    (downloads / "bill.pdf").write_text("x")
    (downloads / "half.crdownload").write_text("x")  # still downloading: ignored
    w.tick()
    assert alerts(svc) == ["New in Downloads: bill.pdf."]
    svc.settings.watch_folders = [{"path": str(downloads), "goal": "file new PDFs into Documents/PDFs"}]
    (downloads / "tax.pdf").write_text("x")
    w.tick()
    assert started and started[0].startswith("file new PDFs into Documents/PDFs") and "tax.pdf" in started[0]


def test_watchers_in_settings(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    page = client.get("/settings").text
    assert "Battery below 20%" in page and "Watch a folder" in page
    assert services.settings.watch_battery and services.settings.watch_disk and not services.settings.watch_cpu
    client.post("/settings/watch", data={"key": "watch_cpu", "on": "1"}, headers=h)
    assert services.settings.watch_cpu
    (services.home / "Downloads").mkdir(exist_ok=True)
    client.post("/settings/watch/folder", data={"path": "Downloads", "goal": "sort them"}, headers=h)
    assert services.settings.watch_folders[0]["goal"] == "sort them"
    assert client.post("/settings/watch/folder", data={"path": "~/.ssh"}, headers=h).status_code == 400
    assert client.post("/settings/watch", data={"key": "password_hash", "on": "1"}, headers=h).status_code == 400
