"""M6 step 6.5: Phone control through mobile-mcp (through Other apps), credits, approvals, and tests."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


# -- Item 2: mobile-mcp via Other apps & Credits -------------------------------------------------------------------
def test_item2_credits_list_mobile_mcp_with_apache2_licence():
    root = Path(__file__).resolve().parent.parent
    settings_html = (root / "prometheus" / "web" / "templates" / "v3" / "settings.html").read_text(encoding="utf-8")
    matrix_md = (root / "docs" / "m0" / "DEPENDENCY_MATRIX.md").read_text(encoding="utf-8")

    assert "mobile-mcp" in settings_html and "Apache-2.0" in settings_html
    assert "mobile-mcp" in matrix_md and "1.0.20" in matrix_md and "Apache-2.0" in matrix_md


def test_item2_connector_manager_provides_mobile_mcp_in_other_apps(app_client):
    from prometheus.connectors.manager import ConnectionManager, ensure_mobile_mcp, find_mobile_mcp

    settings = SimpleNamespace(mcp_servers=[])
    entry = ensure_mobile_mcp(settings)
    assert entry["name"] == "mobile-mcp"
    assert entry["kind"] == "command"
    assert "@mobilenext/mobile-mcp" in entry["target"]
    assert entry["enabled"] is True
    assert settings.mcp_servers == [entry]

    class FakeServer:
        def __init__(self, tools):
            self.tools = tools

        def request(self, method, params=None, timeout=120):
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {"tools": self.tools}
            return {"content": [{"type": "text", "text": "ok"}]}

        def notify(self, method, params=None):
            pass

        def close(self):
            pass

    mgr = ConnectionManager(
        settings,
        factory=lambda e: FakeServer([{"name": "mobile_list_available_devices", "description": "List devices"}]),
    )
    conn = find_mobile_mcp(mgr)
    assert conn.name == "mobile-mcp"

    client, services = app_client()
    from tests.conftest import login

    h = {"X-CSRF-Token": login(client)}
    r = client.post("/settings/phone/mobile-mcp", headers=h)
    assert r.status_code == 200
    assert any(e.get("name") == "mobile-mcp" for e in services.settings.mcp_servers)


def test_item2_mcp_connection_call_raw_preserves_image_bytes():
    from prometheus.connectors.mcp_client import Connection

    png_bytes = b"\x89PNG\r\n\x1a\nFAKE"

    class ImgServer:
        def request(self, method, params=None, timeout=120):
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {"tools": [{"name": "mobile_take_screenshot", "annotations": {"readOnlyHint": True}}]}
            return {
                "content": [
                    {"type": "text", "text": "Screenshot captured"},
                    {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png_bytes).decode()},
                ]
            }

        def notify(self, method, params=None):
            pass

        def close(self):
            pass

    conn = Connection("mobile-mcp", ImgServer())
    conn.start()
    raw = conn.call_raw("mobile_take_screenshot", {"device": "dev1"})
    img_item = [c for c in raw["content"] if c.get("type") == "image"][0]
    assert base64.b64decode(img_item["data"]) == png_bytes


# -- Items 3 & 4: Fake mobile-mcp MCP server + approvals + all old phone actions migrated --------------------------
def _fake_png_bytes():
    import io
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (100, 200), color=(30, 60, 90)).save(buf, format="PNG")
    return buf.getvalue()


def _make_fake_mobile_mcp(elements=None, devices=None):
    """In-memory mobile-mcp Connection matching real @mobilenext/mobile-mcp 1.0.20 schemas."""
    from prometheus.connectors.mcp_client import Connection

    png_b64 = base64.b64encode(_fake_png_bytes()).decode()
    default_elements = elements if elements is not None else [
        {"ref": "@e1", "text": "Accept", "clickable": True, "coordinates": {"x": 300, "y": 1850}},
        {"ref": "@e2", "text": "Pay ₹499", "clickable": True, "coordinates": {"x": 500, "y": 2250}},
        {"ref": "@e3", "text": "Battery 84%", "clickable": False, "coordinates": {"x": 200, "y": 300}},
        {"ref": "@e4", "text": "Wi-Fi", "clickable": True, "coordinates": {"x": 200, "y": 400}},
        {"ref": "@e5", "text": "Alarm", "clickable": True, "coordinates": {"x": 200, "y": 2100}},
        {"ref": "@e6", "text": "Timer", "clickable": True, "coordinates": {"x": 600, "y": 2100}},
        {"ref": "@e7", "text": "Send", "clickable": True, "coordinates": {"x": 950, "y": 2050}},
    ]
    devs = devices if devices is not None else [
        {"id": "3C15BH001TW00000", "name": "OnePlus Nord 5", "platform": "android", "type": "real", "state": "online"}
    ]
    calls: list[tuple[str, dict]] = []

    tools = [
        {"name": "mobile_list_available_devices", "annotations": {"readOnlyHint": True}},
        {"name": "mobile_list_apps", "annotations": {"readOnlyHint": True}},
        {"name": "mobile_launch_app", "annotations": {"readOnlyHint": False}},
        {"name": "mobile_open_url", "annotations": {"readOnlyHint": False}},
        {"name": "mobile_take_screenshot", "annotations": {"readOnlyHint": True}},
        {"name": "mobile_list_elements_on_screen", "annotations": {"readOnlyHint": True}},
        {"name": "mobile_click_on_screen_at_coordinates", "annotations": {"readOnlyHint": False}},
        {"name": "mobile_type_keys", "annotations": {"readOnlyHint": False}},
        {"name": "mobile_swipe_on_screen", "annotations": {"readOnlyHint": False}},
        {"name": "mobile_press_button", "annotations": {"readOnlyHint": False}},
        {"name": "mobile_get_screen_size", "annotations": {"readOnlyHint": True}},
    ]

    class Server:
        def __init__(self):
            self.elements = list(default_elements)
            self.devices = list(devs)

        def request(self, method, params=None, timeout=120):
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {"tools": tools}
            name = (params or {}).get("name")
            args = (params or {}).get("arguments") or {}
            calls.append((name, dict(args)))
            if name == "mobile_list_available_devices":
                return {"content": [{"type": "text", "text": json.dumps({"devices": self.devices})}]}
            if name == "mobile_list_apps":
                apps = [
                    {"packageName": "com.android.settings", "name": "Settings"},
                    {"packageName": "com.google.android.deskclock", "name": "Clock"},
                    {"packageName": "com.google.android.calculator", "name": "Calculator"},
                    {"packageName": "com.spotify.music", "name": "Spotify"},
                    {"packageName": "com.google.android.dialer", "name": "Phone"},
                    {"packageName": "com.google.android.apps.messaging", "name": "Messages"},
                    {"packageName": "com.termux", "name": "Termux"},
                ]
                return {"content": [{"type": "text", "text": json.dumps(apps)}]}
            if name == "mobile_take_screenshot":
                return {"content": [{"type": "image", "mimeType": "image/png", "data": png_b64}]}
            if name == "mobile_list_elements_on_screen":
                return {"content": [{"type": "text", "text": json.dumps(self.elements)}]}
            if name == "mobile_get_screen_size":
                return {"content": [{"type": "text", "text": json.dumps({"width": 1080, "height": 2400})}]}
            return {"content": [{"type": "text", "text": f"OK: {name}"}]}

        def notify(self, method, params=None):
            pass

        def close(self):
            pass

    srv = Server()
    conn = Connection("mobile-mcp", srv)
    conn.start()
    return conn, calls, srv


def _wire_mobile_mcp(monkeypatch, elements=None, devices=None):
    import prometheus.agents.apps_agent as apps_agent
    import prometheus.agents.phone_agent as pa
    from prometheus.connectors.manager import ConnectionManager

    conn, calls, srv = _make_fake_mobile_mcp(elements=elements, devices=devices)
    settings = SimpleNamespace(mcp_servers=[{"name": "mobile-mcp", "kind": "command", "target": "npx", "enabled": True}])
    mgr = ConnectionManager(settings)
    mgr.live["mobile-mcp"] = conn
    monkeypatch.setattr(apps_agent, "MANAGER", mgr)
    monkeypatch.setattr(pa, "SERVICES", SimpleNamespace(settings=settings, notify=lambda *a, **k: None))
    return pa, conn, calls, srv


def test_item3_approvals_taps_typing_apps_ask_every_time_and_reading_asks_once(make_ctx, monkeypatch):
    pa, _, calls, _ = _wire_mobile_mcp(monkeypatch)
    from prometheus.models.fake import FakeProvider

    vision = FakeProvider(['{"found": false, "x": 0, "y": 0, "label": "", "answer": "Clock shows 10:30"}'], name="fake")
    ctx = make_ctx(providers=[vision], routes={"vision": ["fake"], "private": ["fake"]})
    agent = pa.PhoneAgent()

    # Reading the screen asks once, then runs straight away after one-time yes
    r1 = agent.run("what's on my phone screen", {"action": "look", "question": "What time?"}, ctx)
    assert r1.status == "waiting_approval"
    ask = ctx.store.asks_for_chat(ctx.chat_id())[-1]
    out1 = ctx.approvals.resolve(ask.id, True, remember="exact")
    assert "Clock shows 10:30" in out1

    r2 = agent.run("battery", {"action": "battery"}, ctx)
    assert r2.status == "done" and "84%" in r2.summary

    # Every tap, type, open_app asks EVERY time (even repeating the exact same action!)
    for inputs, tool_name, snippet in (
        ({"action": "open_app", "app": "Calculator"}, "mobile_launch_app", "calculator"),
        ({"action": "tap", "target": "Accept"}, "mobile_click_on_screen_at_coordinates", "accept"),
        ({"action": "type", "text": "hello world"}, "mobile_type_keys", "hello world"),
    ):
        before = [c for c in calls if c[0] == tool_name]
        res1 = agent.run("act", inputs, ctx)
        assert res1.status == "waiting_approval"
        assert snippet in res1.summary.lower()
        assert [c for c in calls if c[0] == tool_name] == before  # nothing executed before yes!
        pending = ctx.store.approvals(status="pending")
        assert len(pending) == 1
        ctx.approvals.resolve(pending[0].id, True, remember="exact")
        assert len([c for c in calls if c[0] == tool_name]) == len(before) + 1

        # Second time MUST STILL ASK (never remembered!)
        res2 = agent.run("act again", inputs, ctx)
        assert res2.status == "waiting_approval"
        ctx.approvals.resolve(ctx.store.approvals(status="pending")[0].id, False)

    # Termux terminal app & secrets refused before approval
    assert agent.run("x", {"action": "open_app", "app": "termux"}, ctx).status == "failed"
    assert agent.run("x", {"action": "type", "text": "sk-ant-A1b2C3d4E5f6G7h8I9j0K1L2"}, ctx).status == "failed"


def test_item4_all_old_phone_actions_run_via_mobile_mcp(make_ctx, monkeypatch):
    pa, _, calls, srv = _wire_mobile_mcp(monkeypatch)
    from prometheus.models.fake import FakeProvider

    vision = FakeProvider(['{"found": true, "x": 120, "y": 240, "label": "MissingIcon", "answer": "Clock app"}'], name="fake")
    ctx = make_ctx(providers=[vision], routes={"vision": ["fake"], "private": ["fake"]})
    agent = pa.PhoneAgent()

    def run_and_approve(inputs):
        res = agent.run("phone", inputs, ctx)
        while res.status == "waiting_approval":
            pending = ctx.store.approvals(status="pending")
            if not pending:
                break
            out = ctx.approvals.resolve(pending[0].id, True, remember="broad")
            return out
        return res.summary

    # 1. open_app
    out = run_and_approve({"action": "open_app", "app": "Spotify"})
    assert "Spotify" in out
    assert ("mobile_launch_app", {"device": "3C15BH001TW00000", "packageName": "com.spotify.music"}) in calls

    # 2. open_url
    out = run_and_approve({"action": "open_url", "url": "https://wikipedia.org"})
    assert any(t == "mobile_open_url" and a["url"] == "https://wikipedia.org" for t, a in calls)

    # 3. look
    out = run_and_approve({"action": "look", "question": "What app?"})
    assert "Clock app" in out and any(t == "mobile_take_screenshot" for t, _ in calls)

    # 4. tap (element lookup & vision fallback & payment approval)
    run_and_approve({"action": "tap", "target": "Accept"})
    assert any(t == "mobile_click_on_screen_at_coordinates" and a["x"] == 300 and a["y"] == 1850 for t, a in calls)
    pay = agent.run("pay", {"action": "tap", "target": "Pay ₹499"}, ctx)
    assert pay.status == "waiting_approval" and "spend money" in pay.summary.lower()
    ctx.approvals.resolve(ctx.store.approvals(status="pending")[0].id, False)

    # 5. type (normal ok, password box refused!)
    out = run_and_approve({"action": "type", "text": "2+2"})
    assert any(t == "mobile_type_keys" and a["text"] == "2+2" for t, a in calls)
    srv.elements.append({"ref": "@e9", "text": "Password", "password": True, "coordinates": {"x": 300, "y": 600}})
    res = agent.run("type pwd", {"action": "type", "text": "my-pass"}, ctx)
    assert res.status == "waiting_approval"
    pwd_out = ctx.approvals.resolve(ctx.store.approvals(status="pending")[0].id, True)
    assert "password" in pwd_out.lower()
    assert not any(t == "mobile_type_keys" and a.get("text") == "my-pass" for t, a in calls)
    srv.elements.pop()

    # 6. swipe
    run_and_approve({"action": "swipe", "direction": "up"})
    assert any(t == "mobile_swipe_on_screen" and a["direction"] == "up" for t, a in calls)

    # 7. key
    run_and_approve({"action": "key", "key": "recents"})
    assert any(t == "mobile_press_button" and a["button"] == "APP_SWITCH" for t, a in calls)

    # 8. setting
    out = run_and_approve({"action": "setting", "what": "wifi", "value": "on"})
    assert any(t == "mobile_launch_app" and a["packageName"] == "com.android.settings" for t, a in calls)
    assert agent.run("bad", {"action": "setting", "what": "volume", "value": "99"}, ctx).status == "failed"

    # 9. alarm
    out = run_and_approve({"action": "alarm", "time": "6:45 pm", "label": "Gym"})
    assert "18:45" in out
    assert any(t == "mobile_launch_app" and a["packageName"] == "com.google.android.deskclock" for t, a in calls)

    # 10. timer
    out = run_and_approve({"action": "timer", "minutes": 5})
    assert "5" in out

    # 11. message
    out = run_and_approve({"action": "message", "to": "98100 11111", "text": "Running late", "app": "whatsapp"})
    assert any(t == "mobile_open_url" and "wa.me/919810011111" in a["url"] for t, a in calls)

    # 12. call
    out = run_and_approve({"action": "call", "to": "+919810022222"})
    assert any(t == "mobile_launch_app" and a["packageName"] == "com.google.android.dialer" for t, a in calls)

    # 13. notifications & 14. battery
    out_notif = agent.run("notifs", {"action": "notifications"}, ctx)
    assert out_notif.status == "done" and any(t == "mobile_list_elements_on_screen" for t, _ in calls)
    out_bat = agent.run("bat", {"action": "battery"}, ctx)
    assert out_bat.status == "done" and "84%" in out_bat.summary


def test_item4_offline_device_queues_phone_wait_and_phrases(make_ctx, monkeypatch):
    from prometheus import phone_wait
    from prometheus.quick import understand

    pa, _, _, _ = _wire_mobile_mcp(monkeypatch, devices=[])
    ctx = make_ctx()
    res = pa.PhoneAgent().run("bat", {"action": "battery"}, ctx)
    assert res.status == "waiting_approval" and "waits" in res.summary
    assert len(phone_wait.jobs(ctx.store)) == 1

    assert understand("open spotify on my phone").steps[0]["inputs"] == {"action": "open_app", "app": "spotify"}
    assert understand("turn off wifi on my phone").steps[0]["inputs"]["value"] == "off"
    assert understand("set an alarm for 7am on my phone").steps[0]["inputs"]["time"] == "7am"
    assert understand("what's on my phone screen").steps[0]["inputs"]["action"] == "look"
    assert understand("open spotify").steps[0]["agent"] == "system"


# -- Item 5: Old adb control code and its tests removed ------------------------------------------------------------
def test_item5_old_adb_control_code_and_tests_removed():
    root = Path(__file__).resolve().parent.parent
    android_py = (root / "prometheus" / "connectors" / "android.py").read_text(encoding="utf-8")
    agent_py = (root / "prometheus" / "agents" / "phone_agent.py").read_text(encoding="utf-8")

    for forbidden in ("type_escape", "uiautomator", "screencap", "input tap", "input swipe", "input text",
                      "input keyevent", "def tap(", "def type_text(", "def open_app("):
        assert forbidden not in android_py, f"Old adb control code {forbidden!r} still in android.py"
        assert forbidden not in agent_py, f"Old adb control code {forbidden!r} still in phone_agent.py"

    assert not (root / "tests" / "test_v19_android.py").exists()


def test_real_mobile_mcp_prose_and_box_coordinate_parsing():
    from prometheus.connectors.mcp_client import Connection
    from prometheus.phone import MobilePhone

    apps_str = (
        "Found these apps on device: Calculator (com.oneplus.calculator), "
        "Clock (com.oneplus.deskclock), Notes (com.oneplus.note), Settings (com.android.settings)"
    )
    els_str = (
        'Found these elements on screen: [{"ref":"@e1","type":"android.widget.Button",'
        '"text":"7","coordinates":{"x":100,"y":200,"width":80,"height":60}}]'
    )

    class RealFmtServer:
        def request(self, method, params=None, timeout=120):
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {
                    "tools": [
                        {"name": "mobile_list_available_devices"},
                        {"name": "mobile_list_apps"},
                        {"name": "mobile_list_elements_on_screen"},
                    ]
                }
            tool = (params or {}).get("name")
            if tool == "mobile_list_available_devices":
                return {"content": [{"type": "text", "text": json.dumps({"devices": [{"id": "dev1", "state": "online"}]})}]}
            if tool == "mobile_list_apps":
                return {"content": [{"type": "text", "text": apps_str}]}
            return {"content": [{"type": "text", "text": els_str}]}

        def notify(self, method, params=None):
            pass

        def close(self):
            pass

    conn = Connection("mobile-mcp", RealFmtServer())
    conn.start()
    p = MobilePhone(conn=conn)
    assert p.find_app("Calculator") == "com.oneplus.calculator"
    assert p.find_app("Clock") == "com.oneplus.deskclock"
    assert p.find_app("Notes") == "com.oneplus.note"
    el = p.find("7")
    assert el is not None and el["x"] == 140 and el["y"] == 230 and el["ref"] == "@e1"

