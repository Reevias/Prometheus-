import os
import time

from prometheus import health
from prometheus.quick import understand

SUNDAY_7PM = time.mktime((2026, 11, 15, 19, 30, 0, 0, 0, -1))


class Laptop:
    def health_info(self):
        return {"startup": ["Spotify", "Discord", "Steam", "Teams", "OneDrive", "Zoom", "Epic"], "design": 50000,
                "full": 41000, "cycles": 312, "updates": 3, "onedrive": True}


def test_report_and_fixes(app_client, monkeypatch):
    _, services = app_client()
    dl = services.home / "Downloads"
    dl.mkdir(exist_ok=True)
    big = dl / "old-movie.mkv"
    with open(big, "wb") as f:
        f.truncate(health.BIG + 1)
    old = time.time() - 200 * 86400
    os.utime(big, (old, old))
    monkeypatch.setattr(health, "temp_size", lambda now: (500 * 1024 ** 2, 40))
    from collections import namedtuple

    usage = namedtuple("U", "total used free")
    monkeypatch.setattr(health.shutil, "disk_usage", lambda p: usage(500 * 1024 ** 3, 300 * 1024 ** 3, 200 * 1024 ** 3))
    report, fixes = health.check(services, Laptop())
    assert "old-movie.mkv" in report and "Battery health: 82%" in report and "updates waiting: 3" in report
    kinds = {f["kind"]: f["big"] for f in fixes}
    assert kinds == {"recycle_files": True, "health_clean": True, "health_fix": False}
    health.post(services, Laptop())
    assert any(a.kind == "recycle_files" for a in services.store.big_approvals())       # deleting: Approvals page
    chat = [c for c, t in services.store.chats() if t == "Laptop health"][0]
    asks = services.store.asks_for_chat(chat)
    assert {a.payload["page"] for a in asks} == {"startup", "updates"}                    # opening pages: in chat


def test_weekly_schedule(app_client, monkeypatch):
    _, services = app_client()
    monkeypatch.setattr(health, "check", lambda s, laptop=None, now=None: ("Weekly laptop health check: all good.", []))
    clock = services.briefing
    clock.notify = None
    services.settings.briefing_morning_on = services.settings.briefing_evening_on = False
    services.settings.study_reminder = ""
    assert clock.health_check(SUNDAY_7PM) == ["health"]
    assert clock.health_check(SUNDAY_7PM + 600) == []                                     # once a week
    assert clock.health_check(SUNDAY_7PM - 86400 * 2) == []                               # not on Friday


def test_clear_temp_only_old_files(tmp_path, monkeypatch):
    monkeypatch.setattr(health.tempfile, "gettempdir", lambda: str(tmp_path))
    (tmp_path / "new.tmp").write_text("x")
    old = tmp_path / "old.tmp"
    old.write_text("x" * 100)
    os.utime(old, (time.time() - 30 * 86400,) * 2)
    assert "Cleared 1" in health.clear_temp() and (tmp_path / "new.tmp").exists() and not old.exists()


def test_phrase():
    assert understand("check my laptop health").steps[0]["agent"] == "health"
