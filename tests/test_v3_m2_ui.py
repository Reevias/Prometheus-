"""M2.1: the new interface (look, Home, chat, command bar, right panel). Replaces the first M2 tests, which checked
for made-up values ("System Monitoring 100%", always "online", the name "Saiveer", a Control PC command)."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
from conftest import login

from prometheus.services.telemetry import telemetry_service
from prometheus.services.ui_state import COMMANDS, UIStateService

WEB = Path(__file__).resolve().parents[1] / "prometheus" / "web"


# ---- real readings only -------------------------------------------------------------------------------------

def test_telemetry_is_real_or_missing():
    t = telemetry_service.get_telemetry()
    for key in ("cpu", "ram", "gpu", "temp_c", "disk", "net", "uptime_seconds", "platform"):
        assert key in t
    assert t["cpu"] is None or 0 <= t["cpu"] <= 100
    if t["ram"] is not None:
        assert 0 < t["ram"]["total_gb"] and 0 <= t["ram"]["percent"] <= 100
    assert t["gpu"] is None or (t["gpu"]["model"] and 0 <= t["gpu"]["percent"] <= 100)
    assert t["uptime_seconds"] > 0


def test_network_speed_needs_two_readings():
    from prometheus.services.telemetry import SystemTelemetryService
    svc = SystemTelemetryService()
    assert svc.get_network() is None                          # no fake 0 KB/s on the first reading
    time.sleep(0.05)
    net = svc.get_network()
    assert net is None or (net["down_kbps"] >= 0 and net["up_kbps"] >= 0)


def test_idle_state_invents_nothing():
    s = UIStateService()                                       # no services: nothing known
    assert s.get_current_task() is None and s.get_active_agent() is None
    state = s.get_full_state()
    assert state["online"] is False and state["current_task"] is None


def test_online_needs_a_working_ai_and_not_paused(app_client):
    client, services = app_client()
    login(client)
    st = client.get("/api/v3/state").json()
    assert st["online"] is True and st["current_task"] is None and st["active_agent"] is None
    services.settings.paused = True
    st = client.get("/api/v3/state").json()
    assert st["online"] is False and st["status"] == "paused"


def test_commands_are_real_actions():
    names = {c["command"] for c in COMMANDS}
    assert "/pc" not in names                                   # Control PC is M6: hidden, never a dead command
    for c in COMMANDS:
        assert c["kind"] in ("fill", "go", "do") and c["description"]
        if c["kind"] == "go":
            assert c["value"].startswith("/")


# ---- pages ----------------------------------------------------------------------------------------------------

def test_home_has_the_screenshot_layout(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/").text
    for part in ('class="app"', 'class="sidebar"', 'class="topbar', 'id="panel"', 'data-sphere="hero"',
                 'id="command"', 'id="plus"', 'id="send"', 'class="wave"', ">Prometheus<"):
        assert part in page, part
    assert "slash-indicator" not in page                       # the permanent "/" button is gone: + replaces it
    assert "Saiveer" not in page and "System Monitoring" not in page
    assert "Control PC" not in page                            # hidden until M6
    assert "Add your name" in page                             # the owner's own name, asked for


def test_sidebar_links_all_lead_to_real_pages(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/").text
    nav = re.search(r'<nav class="nav".*?</nav>', page, re.S).group(0)
    links = re.findall(r'href="([^"]+)"', nav)
    assert links[0] == "/" and len(links) == len(set(links))   # no two items lead to the same place
    for href in links:
        r = client.get(href)
        assert r.status_code == 200, href
    labels = re.findall(r"<span>([^<]+)</span></a>", nav)
    assert "Projects" not in labels and "Automations" not in labels   # not built yet: hidden, not dead


def test_send_starts_a_chat_and_shows_the_answer(app_client):
    from prometheus.models.fake import FakeProvider
    client, services = app_client(providers=[FakeProvider(['{"steps": [], "answer": "Hi there, owner."}'], name="fake")])
    csrf = login(client)
    r = client.post("/api/v3/send", data={"text": "hello"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    chat_id = r.json()["chat_id"]
    assert r.json()["url"] == f"/c/{chat_id}"
    for _ in range(100):
        if chat_id not in services.running and len(services.store.messages(chat_id)) >= 2:
            break
        time.sleep(0.05)
    page = client.get(f"/c/{chat_id}").text
    assert "hello" in page and "Hi there, owner." in page
    assert client.get(f"/c/{chat_id}/messages").status_code == 200


def test_empty_message_is_refused(app_client):
    client, _ = app_client()
    csrf = login(client)
    r = client.post("/api/v3/send", data={"text": "  "}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400 and "Type something" in r.json()["error"]


def test_attachments_saved_and_limits_enforced(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    r = client.post("/api/v3/send", data={"text": ""}, files=[("files", ("notes.md", b"# hi", "text/markdown"))], headers=h)
    assert r.status_code == 200                                 # files with no words are fine: Prometheus asks
    r = client.post("/api/v3/send", data={"text": "x"}, files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))], headers=h)
    assert r.status_code == 400 and "can't take" in r.json()["error"]
    services.settings.attach_max_files = 1
    many = [("files", (f"a{i}.txt", b"x", "text/plain")) for i in range(2)]
    r = client.post("/api/v3/send", data={"text": "x"}, files=many, headers=h)
    assert r.status_code == 400 and "Up to 1" in r.json()["error"]


def test_zip_is_listed_never_unpacked(tmp_path):
    import io
    import zipfile

    from prometheus import attachments
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.py", "print(1)")
        z.writestr("../../evil.txt", "x")
    p = attachments.save(tmp_path, "code.zip", buf.getvalue(), 25)
    text = attachments.request_text("", [p])
    assert "a.py" in text and "not unpacked" in text
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()


def test_old_attachments_are_cleaned(tmp_path):
    from prometheus import attachments
    old = attachments.folder(tmp_path) / "2020-01-01"
    old.mkdir(parents=True)
    (old / "x.txt").write_text("x")
    keep = attachments.folder(tmp_path) / time.strftime("%Y-%m-%d")
    keep.mkdir(parents=True)
    assert attachments.clean_old(tmp_path, 7) == 1 and not old.exists() and keep.exists()


def test_stop_ends_only_this_chats_reply(app_client):
    from prometheus.models.fake import FakeProvider
    gate = threading.Event()

    def slow(messages, system):
        gate.wait(5)
        return '{"steps": [], "answer": "late answer"}'

    client, services = app_client(providers=[FakeProvider(slow, name="fake")])
    csrf = login(client)
    chat_id = client.post("/api/v3/send", data={"text": "think hard"}, headers={"X-CSRF-Token": csrf}).json()["chat_id"]
    time.sleep(0.2)
    r = client.post(f"/api/v3/chats/{chat_id}/stop", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["stopping"] is True
    gate.set()
    for _ in range(100):
        if chat_id not in services.running:
            break
        time.sleep(0.05)
    texts = [m.text for m in services.store.messages(chat_id)]
    assert any(t.startswith("Stopped") for t in texts) and "late answer" not in texts
    assert not services.killswitch.is_set and not services.settings.paused     # not Stop all


def test_profile_name_and_notifications(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/v3/profile", data={"name": "Amit <b>"}, headers=h).json()["name"] == "Amit b"
    assert "Amit b" in client.get("/").text
    services.notify("task", "Your summary is ready")
    assert client.get("/api/v3/state").json()["unread"] == 1
    notes = client.get("/api/v3/notifications").json()
    assert notes[0]["text"] == "Your summary is ready"
    client.post("/api/v3/notifications/read", headers=h)
    assert client.get("/api/v3/state").json()["unread"] == 0


def test_window_buttons_refuse_outside_the_desktop_window(app_client):
    client, _ = app_client()
    csrf = login(client)
    r = client.post("/api/v3/window/minimize", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409


def test_classic_view_still_reachable_until_switch_over(app_client):
    client, _ = app_client()
    login(client)
    assert 'id="messages"' in client.get("/?view=classic").text


# ---- design rules ---------------------------------------------------------------------------------------------

def test_colours_only_in_the_token_file():
    """Every colour is defined once in tokens.css; the rest of the interface uses var(--…)."""
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d")
    for f in ("static/v3/theme.css", "static/v3/app.js", "static/v3/sphere.js"):
        text = (WEB / f).read_text(encoding="utf-8")
        assert not colour.findall(text), f"{f} writes colours directly: {colour.findall(text)[:5]}"
    tokens = (WEB / "static/v3/tokens.css").read_text(encoding="utf-8")
    hexes = {h.upper() for h in re.findall(r"#[0-9a-fA-F]{6}", tokens)}
    assert {"#0B0B10", "#17171F", "#211D1A", "#FF922B", "#FFC06A", "#F4F1ED", "#AAA5A0"} <= hexes
    assert len(hexes) <= 9                                       # the palette plus the two status colours


def test_nothing_downloaded_from_the_internet():
    for f in ("static/v3/theme.css", "static/v3/tokens.css", "static/v3/app.js", "static/v3/sphere.js",
              "templates/v3/layout.html"):
        text = (WEB / f).read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text and "@import" not in text, f


def test_reduced_motion_respected():
    css = (WEB / "static/v3/theme.css").read_text(encoding="utf-8")
    js = (WEB / "static/v3/sphere.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css and "prefers-reduced-motion" in js
    assert "visibilitychange" in js and '"blur"' in js           # the sphere pauses when hidden or unfocused
