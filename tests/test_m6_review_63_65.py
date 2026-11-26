"""Review of M6 6.3–6.5 (Opus, 26 Nov 2026). Each test failed before its fix."""
from __future__ import annotations


def test_approvals_waiting_across_a_restart_still_have_a_handler(app_client):
    """pc_control_read / pc_control_input / phone_read were registered only when their agent first ran, so a yes
    given after a restart (before the agent ran again) failed with "No handler"; Stop all's hook for PC control was
    also missing until then."""
    _, services = app_client()
    handlers = services.approvals._handlers
    for kind in ("pc_control_read", "pc_control_input", "phone_read", "phone_action"):
        assert kind in handlers, kind
    assert getattr(services.approvals, "_pc_stop_hooked", False)


# -- 6.4a PC control --------------------------------------------------------------------------------------------------
def test_a_window_title_can_never_break_out_of_the_powershell_script():
    """PowerShell also treats the curly quotes ‘ ’ ‚ ‛ as single quotes; only ' was doubled, so a window title chosen
    by the AI could end the string and run its own command."""
    import re

    from prometheus.pc_control.uia import _build_read_script

    evil = "x’; Start-Process calc; ’ ‘ ‚ ‛ \" ` $(calc)"
    script = _build_read_script(evil, 5)
    assert "calc" not in script and not any(q in script for q in "‘’‚‛")
    m = re.search(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", script)
    import base64

    assert m and base64.b64decode(m.group(1)).decode("utf-8") == evil.strip()


def test_the_approval_shows_every_character_that_will_be_typed():
    from prometheus.pc_control.agent import _describe_input

    text = "hello there " + "x" * 40 + " rm everything"
    summary, payload = _describe_input("type", {"text": text})
    assert text in summary and payload["text"] == text


def test_keys_that_write_text_are_refused_in_a_password_field(tmp_path):
    """press_keys skipped the password-field check, so "1" or Ctrl+V could go into a password box one key at a time."""
    import pytest

    from prometheus.pc_control.controller import PCController
    from prometheus.pc_control.errors import PasswordFieldError
    from tests.test_v3_m6_pc_control import _make_fake_backend

    backend, state = _make_fake_backend(fg_idx=0)
    ctrl = PCController(data_dir=tmp_path, backend=backend, password_checker=lambda **k: (True, "password box"))
    for keys in ("a", "1", "ctrl+v", "shift+insert", "space"):
        with pytest.raises(PasswordFieldError):
            ctrl.press_keys(keys)
    ctrl.press_keys("tab")                                   # moving on from the box is still fine
    assert [e for e in state["events"] if e[0] == "keys"] and len([e for e in state["events"] if e[0] == "keys"]) == 1


def test_a_shrunk_screenshot_says_how_to_turn_its_pixels_into_screen_points(tmp_path, monkeypatch):
    """Screens wider than 1600 px are shrunk, but clicks use the real screen: the AI must be told the scale."""
    from PIL import Image

    from prometheus.pc_control import agent
    from prometheus.pc_control.controller import PCController
    from tests.test_v3_m6_pc_control import _make_fake_backend

    backend, _ = _make_fake_backend(vscreen=(-1920, 0, 3840, 1080))
    raw = Image.new("RGB", (3840, 1080))
    monkeypatch.setattr(agent, "make_controller", lambda d, killswitch=None: PCController(d, backend=backend, grabber=lambda: raw))
    said = agent._execute_read({"action": "screenshot", "data_dir": str(tmp_path)})
    assert "1600x450" in said and "x 2.4" in said and "-1920" in said


# -- 6.4b control the laptop from the phone ------------------------------------------------------------------------------
from prometheus import remote_control as rc  # noqa: E402
from tests.test_m6_remote_control import confirm, pair, rig, run  # noqa: E402,F401  (fixture and helpers)


def test_typed_text_from_the_phone_never_reaches_a_pop_up(rig):
    """The laptop yes shows the whole text (that is what you approve), but the Windows notification only says how
    many characters, so a PIN typed from the phone doesn't pop up on screen."""
    client, services, h, pc, notes = rig
    phone, auth, _ = pair(rig)
    started = run(phone, auth, "type", text="my bank pin is 4321 and more").json()
    confirm(phone, auth, started)
    assert notes and all("4321" not in text for _, text in notes)
    [waiting] = [a for a in services.store.approvals() if a.kind == rc.APPROVAL_KIND]
    assert "my bank pin is 4321 and more" in waiting.summary            # the yes covers every character


def test_laptop_actions_from_the_phone_respect_the_apps_switch(rig):
    client, services, h, pc, notes = rig
    services.settings.features_off = ["apps"]
    phone, auth, _ = pair(rig)
    started = run(phone, auth, "app_open", name="notepad").json()
    got = confirm(phone, auth, started).json()
    assert got["state"] != "done" and not [c for c in services.laptop.calls if c[0] == "app_open"]


def test_a_waiting_laptop_yes_does_nothing_after_remote_control_is_switched_off(rig):
    client, services, h, pc, notes = rig
    phone, auth, _ = pair(rig)
    started = run(phone, auth, "click", x=10, y=20).json()
    confirm(phone, auth, started)
    services.settings.remote_control = False
    said = services.remote.run_approved({"request": started["id"]})
    assert not pc["input"] and "off" in said.lower()


# -- 6.5 phone control through mobile-mcp ---------------------------------------------------------------------------
from tests.test_v3_m6_mobile_mcp import _wire_mobile_mcp  # noqa: E402


def test_changing_the_mobile_mcp_command_needs_the_password(app_client):
    """It is a program Prometheus starts on every phone action; "Other apps" already asks for the password."""
    from conftest import login

    from prometheus.connectors.manager import MOBILE_MCP_TARGET

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    r = client.post("/settings/phone/mobile-mcp", data={"target": "evil.exe --stdio"}, headers=h)
    assert not any("evil.exe" in (e.get("target") or "") for e in services.settings.mcp_servers)
    assert r.status_code in (401, 403)
    assert client.post("/settings/phone/mobile-mcp", headers=h).status_code == 200      # the default needs no password
    assert "@mobilenext/mobile-mcp@1.0.20" in MOBILE_MCP_TARGET                          # pinned, as in the credits


def test_the_phone_wait_check_works_with_mobile_mcp(monkeypatch):
    """phone_back called the removed adb shell(): AttributeError, so queued phone requests never resumed."""
    from prometheus import phone_wait
    from prometheus.phone import MobilePhone

    _, conn, calls, _ = _wire_mobile_mcp(monkeypatch)
    assert phone_wait.phone_back(MobilePhone(conn=conn)) is True
    assert phone_wait.phone_back(MobilePhone("1.2.3.4:5555", conn=conn)) is True


def test_nothing_on_the_phone_screen_is_read_before_the_one_time_yes(make_ctx, monkeypatch):
    pa, conn, calls, _ = _wire_mobile_mcp(monkeypatch)
    ctx = make_ctx()
    agent = pa.PhoneAgent()
    calls.clear()
    tap = agent.run("tap accept", {"action": "tap", "target": "Accept"}, ctx)
    bat = agent.run("battery", {"action": "battery"}, ctx)
    read_tools = {"mobile_list_elements_on_screen", "mobile_take_screenshot"}
    assert not [t for t, _ in calls if t in read_tools], calls
    assert tap.status == "waiting_approval" and bat.status == "waiting_approval"
    assert [a.kind for a in ctx.store.approvals()] == ["phone_read", "phone_read"] or \
        all(a.kind == "phone_read" for a in ctx.store.asks_for_chat(ctx.chat_id()))


def test_stop_all_stops_the_next_step_of_a_phone_action(monkeypatch):
    """A call is several steps (open the dialer, type, find Call, tap): Stop all must stop the rest."""
    import pytest

    from prometheus.connectors.android import PhoneError
    from prometheus.phone import MobilePhone
    from prometheus.safety.killswitch import KillSwitch

    pa, conn, calls, _ = _wire_mobile_mcp(monkeypatch)
    ks = KillSwitch()
    pa.SERVICES.killswitch = ks
    p = MobilePhone(conn=conn)
    real = conn.call

    def stop_after_first(tool, args=None):
        out = real(tool, args)
        ks.trigger("test")
        return out

    monkeypatch.setattr(conn, "call", stop_after_first)
    with pytest.raises(PhoneError):
        p.call("9876543210")
    assert not [t for t, _ in calls if t == "mobile_click_on_screen_at_coordinates"]


def test_an_approved_tap_only_lands_on_what_was_approved(monkeypatch):
    """A tap waits for its yes (even across a restart); if the approved button isn't there any more it is refused."""
    import pytest

    pa, conn, calls, srv = _wire_mobile_mcp(monkeypatch)
    payload = {"action": "tap", "x": 300, "y": 1850, "label": "Accept", "ref": "@e1"}
    srv.elements = [{"ref": "@e9", "text": "Pay ₹499", "clickable": True, "coordinates": {"x": 300, "y": 1850}}]
    with pytest.raises(RuntimeError):
        pa.action_handler(payload)
    assert not [t for t, _ in calls if t == "mobile_click_on_screen_at_coordinates"]
    srv.elements = [{"ref": "@e1", "text": "Accept", "clickable": True, "coordinates": {"x": 305, "y": 1849}}]
    assert "Tapped" in pa.action_handler(payload)


def test_phone_approvals_show_exactly_what_will_happen(make_ctx, monkeypatch):
    pa, conn, calls, _ = _wire_mobile_mcp(monkeypatch)
    ctx = make_ctx()
    agent = pa.PhoneAgent()
    long = "hi " + "x" * 300 + " end of message"
    agent.run("type", {"action": "type", "text": long}, ctx)
    agent.run("wa", {"action": "message", "to": "9876543210", "text": "hello"}, ctx)
    agent.run("sms", {"action": "message", "to": "9876543210", "text": "hello there", "app": "sms"}, ctx)
    summaries = [a.summary for a in ctx.store.approvals()]
    assert any(long in s for s in summaries)
    assert any("919876543210" in s for s in summaries if "WhatsApp" in s)        # the number that is really used
    assert any("you type" in s.lower() for s in summaries if "SMS" in s)          # the SMS text isn't filled in


def test_the_spoken_brief_reads_phone_notifications_only_after_the_read_yes(app_client, monkeypatch):
    """The voice brief read every label on the phone screen by itself; reading the phone needs the one-time yes."""
    import prometheus.connectors.android as android
    from prometheus.voice import brief

    _, services = app_client()
    services.settings.phone_target = "100.64.0.9:5555"
    read = []

    class FakePhone:
        def __init__(self, target=""):
            pass

        def notifications(self):
            read.append(1)
            return [{"app": "com.whatsapp", "title": "Mum", "text": ""}]

    monkeypatch.setattr(android, "Phone", FakePhone)
    assert "on your phone" not in brief._messages(services) and read == []
    services.store.add_allow_rule("phone_read", "phone:read", "Reading the phone screen and notifications")
    assert "1 notification on your phone" in brief._messages(services) and read == [1]


# -- 6.3 Cloudroom storage --------------------------------------------------------------------------------------------
from tests.test_m6_cloudroom_storage import KEY, SALT, FakeDrive  # noqa: E402


def _storage(tmp_path, drive=None, store=None):
    from prometheus import cloudroom_storage as cs
    from prometheus.memory.store import Store

    return cs.Storage(store or Store(tmp_path / "memory.db"), tmp_path / "outbox", drive=drive or FakeDrive(),
                      key=lambda: (SALT, KEY))


def test_drive_never_gets_a_fingerprint_of_the_plain_content(tmp_path):
    """appProperties held SHA-256 of the plain text: anyone reading the Drive could check a guess at a short note."""
    import hashlib

    drive = FakeDrive()
    st = _storage(tmp_path, drive)
    plain = b'{"chat_id": 5, "title": "hi", "messages": []}'
    st.queue("private", "private-chat-5.pcr", plain)
    st.flush()
    [f] = drive.files.values()
    assert f["sha"] and f["sha"] != hashlib.sha256(plain).hexdigest()


def test_a_sealed_item_cant_be_swapped_onto_another_name(tmp_path):
    import pytest

    from prometheus import backup
    from prometheus import cloudroom_storage as cs

    drive = FakeDrive()
    st = _storage(tmp_path, drive)
    st.queue("private", "private-chat-5.pcr", b'{"chat_id": 5}')
    st.queue("private", "private-chat-7.pcr", b'{"chat_id": 7}')
    st.flush()
    by_name = {f["name"]: f for f in drive.files.values()}
    by_name["private-chat-5.pcr"]["bytes"] = by_name["private-chat-7.pcr"]["bytes"]       # someone swaps the files
    with pytest.raises(cs.StorageError):
        st.lookup_private(5)
    sealed = backup.encrypt_bytes(b"x", SALT, KEY)
    with pytest.raises(backup.BackupError):                                               # nothing after the end
        backup.decrypt_bytes(sealed + b"extra", KEY, SALT)


def test_the_offline_outbox_keeps_only_the_newest_backups(tmp_path):
    from prometheus import backup

    drive = FakeDrive()
    drive.online = False
    st = _storage(tmp_path, drive)
    for i in range(backup.KEEP + 6):
        f = tmp_path / f"b{i}.pbak"
        f.write_bytes(backup.encrypt_bytes(f"backup {i}".encode(), SALT, KEY))
        st.queue_sealed("backups", f"prometheus-{i:03d}.pbak", f)
    (tmp_path / "outbox" / "leftover.tmp").write_bytes(b"half")
    st.flush()
    waiting = [n for n in st.pending() if n.endswith(".pbak")]
    assert len(waiting) == backup.KEEP and "prometheus-019.pbak" in waiting and "prometheus-000.pbak" not in waiting
    assert not list((tmp_path / "outbox").glob("*.tmp"))


def test_a_chat_no_longer_left_out_or_deleted_is_removed_from_drive(tmp_path):
    from types import SimpleNamespace

    from prometheus.memory.store import Store

    store = Store(tmp_path / "memory.db")
    drive = FakeDrive()
    st = _storage(tmp_path, drive, store)
    a, b = store.create_chat("left out A"), store.create_chat("left out B")
    left = {a, b}
    services = SimpleNamespace(archive=SimpleNamespace(left_out=lambda cid: cid in left))
    st.snapshot(services)
    st.flush()
    assert {f"private-chat-{a}.pcr", f"private-chat-{b}.pcr"} <= set(drive.in_folder("Private notes"))
    left.discard(a)                       # taken off the "left out" list
    store.delete_chat(b)                  # deleted
    st.snapshot(services)
    st.flush()
    assert not set(drive.in_folder("Private notes"))


def test_every_storage_object_shares_one_lock():
    """"Backup now" made its own Storage with its own lock, so it could upload the same outbox as the nightly job."""
    from pathlib import Path

    from prometheus import cloudroom_storage as cs

    one, two = cs.Storage(None, Path("x")), cs.Storage(None, Path("y"))
    assert one._lock is two._lock
    assert cs.enabled(None) is False                       # no settings: the switch counts as off
