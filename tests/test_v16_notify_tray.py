import time
from types import SimpleNamespace

from prometheus.app import Services
from prometheus.desktop import attach_tray
from prometheus.tray import Tray, icon_image
from tests.conftest import login


def test_notifications_follow_the_switches(app_client):
    client, services = app_client()
    shown = []
    services.notifier = shown.append
    assert services.notify("task", "Done: x") and shown == ["Done: x"]
    services.settings.notify_tasks = False
    assert not services.notify("task", "Done: y") and shown == ["Done: x"]
    h = {"X-CSRF-Token": login(client)}
    client.post("/settings/notifications", data={"on": ["notify_tasks", "notify_waiting"]}, headers=h)
    s = services.settings
    assert s.notify_tasks and s.notify_waiting and not s.notify_briefing and not s.tray


def test_long_tasks_and_waiting_tasks_notify(app_client):
    _, services = app_client()
    shown = []
    services.notifier = shown.append
    chat = services.store.create_chat("t")
    task = services.store.create_task(chat, "sort my downloads")
    outcome = SimpleNamespace(task_id=task, status="done")
    services.after_task(outcome, "sort my downloads", took=5)
    assert shown == []                                         # quick tasks stay quiet
    services.after_task(outcome, "sort my downloads", took=45)
    assert shown == ["Done: sort my downloads"]
    step = services.store.add_step(task, "files", "move", {})
    services.store.finish_step(step, "waiting_approval", "asked")
    services.after_task(outcome, "sort my downloads", took=2)
    assert shown[-1] == "Waiting for your yes: sort my downloads"


class FakeIcon:
    def __init__(self, name, image, title, menu):
        self.menu, self.stopped, self.notes = menu, False, []

    def run(self):
        pass

    def stop(self):
        self.stopped = True

    def notify(self, text, title):
        self.notes.append(text)


class FakePystray:
    Icon = FakeIcon

    @staticmethod
    def MenuItem(label, action, default=False):
        return (label, action, default)

    @staticmethod
    def Menu(*items):
        return list(items)


class Events:
    def __init__(self):
        self.closing = []

    def __iadd__(self, fn):
        return self


class FakeWindow:
    def __init__(self):
        self.visible, self.destroyed, self.handlers = True, False, []
        self.events = SimpleNamespace(closing=self)

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def show(self):
        self.visible = True

    def hide(self):
        self.visible = False

    def destroy(self):
        self.destroyed = True


def test_closing_hides_to_the_tray_and_quit_really_quits():
    window = FakeWindow()
    tray = attach_tray(window, SimpleNamespace(tray=True), pystray=FakePystray)
    time.sleep(0.05)
    assert [m[0] for m in tray.icon.menu] == ["Open Prometheus", "Hide window", "Quit Prometheus"]
    closing = window.handlers[0]
    assert closing() is False and not window.visible and tray.icon.notes          # hidden, not closed
    tray.open()
    assert window.visible
    tray.quit()
    assert window.destroyed and tray.icon.stopped and closing() is True
    assert attach_tray(FakeWindow(), SimpleNamespace(tray=False), pystray=FakePystray) is None
    assert icon_image().size == (64, 64)


def test_icon_files_and_shortcut_use_them():
    from pathlib import Path

    from prometheus import launcher

    assert launcher.ICON.exists() and launcher.ICON.read_bytes()[:4] == b"\x00\x00\x01\x00"     # a real .ico
    calls = []

    class Done:
        returncode = 0

    def run(cmd, env=None, **kw):
        calls.append(env)
        return Done()

    launcher.make_shortcuts(run=run, windows=True)
    assert calls and all(c["PROM_ICON"].endswith("prometheus.ico") for c in calls)
    assert "IconLocation" in launcher.SHORTCUT_PS
    assert Path(launcher.__file__).with_name("web").joinpath("static", "favicon.png").exists()
    assert icon_image(32).size == (32, 32)


def test_pages_have_the_icon(app_client):
    client, _ = app_client()
    assert "/static/favicon.png" in client.get("/login").text
    assert client.get("/static/icon.png").status_code == 200
