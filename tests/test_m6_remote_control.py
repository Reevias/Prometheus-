"""M6 step 6.4b: control the laptop from the phone. Only a paired device key over the private link; every action asks
on the phone (a separate, single-use yes); the 6.4a mouse/keyboard actions also wait for a yes on the laptop; every
step is in the activity log. Windows itself is faked here: the real actions are NOT VERIFIED on Windows."""
from __future__ import annotations

import json
import time

import pytest
from conftest import login
from fastapi.testclient import TestClient

from prometheus import remote_control as rc
from prometheus.features import BY_KEY, on
from prometheus.config import Settings
from prometheus.server import create_app


class FakeLaptop:
    """Records every call instead of touching Windows."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(*args, **kw):
            self.calls.append((name, args, kw))
            return {"battery": {"percent": 80}, "apps_running": [{"name": "calc", "title": "Calculator"}]}.get(name, "ok")
        return call


@pytest.fixture
def rig(app_client, monkeypatch):
    client, services = app_client()
    services.settings.remote_control = True
    services.settings.allow_lan = True              # the phone reaches the laptop by its private address (existing switch)
    services.laptop = FakeLaptop()
    pc = {"read": [], "input": []}
    monkeypatch.setattr(rc, "PC_READ", lambda payload, killswitch=None: pc["read"].append(payload) or "screen ok")
    monkeypatch.setattr(rc, "PC_INPUT", lambda payload, killswitch=None: pc["input"].append(payload) or "typed ok")
    notes = []
    services.notify = lambda kind, text: notes.append((kind, text)) or True
    csrf = login(client)
    return client, services, {"X-CSRF-Token": csrf}, pc, notes


def phone_client(services, ip="100.64.0.7"):
    return TestClient(create_app(services), base_url="http://100.64.0.7:8000", client=(ip, 50000))


def pair(rig, name="My phone", ip="100.64.0.7"):
    client, services, h, *_ = rig
    code = client.post("/api/v3/remote/pairing-code", headers=h).json()["code"]
    phone = phone_client(services, ip)
    got = phone.post("/api/remote/pair", json={"code": code, "name": name})
    assert got.status_code == 200, got.text
    return phone, {"Authorization": f"Bearer {got.json()['key']}"}, got.json()


def run(phone, auth, action, **params):
    return phone.post("/api/remote/run", json={"action": action, "params": params}, headers=auth)


def confirm(phone, auth, started):
    return phone.post(f"/api/remote/requests/{started['id']}/confirm", json={"confirm": started["confirm"]}, headers=auth)


def audit_actions(services):
    return [a["action"] for a in services.store.audit_log(200)]


# -- item 2: only the paired device key, only over the private link -------------------------------------------------------
@pytest.mark.parametrize("host,ok", [("100.64.0.1", True), ("100.127.255.254", True), ("100.128.0.1", False),
                                     ("100.63.255.255", False), ("192.168.1.5", False), ("10.0.0.4", False),
                                     ("203.0.113.9", False), ("8.8.8.8", False), ("fd7a:115c:a1e0::1", True),
                                     ("2001:db8::1", False), ("127.0.0.1", True), ("::1", True), ("", False),
                                     ("not-an-address", False)])
def test_only_the_private_link_addresses_are_accepted(host, ok):
    assert rc.private_link_ok(host, {}) is ok


def test_a_request_that_came_through_a_proxy_is_not_the_private_link():
    for header in ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host"):
        assert rc.private_link_ok("127.0.0.1", {header: "8.8.8.8"}) is False
        assert rc.private_link_ok("100.64.0.9", {header: "1.2.3.4"}) is False


def test_pairing_gives_one_key_and_the_laptop_keeps_only_a_hash(rig):
    phone, auth, got = pair(rig)
    key = auth["Authorization"].split(" ", 1)[1]
    assert got["device_id"] and got["key"] == key
    client, services, *_ = rig
    assert key not in json.dumps(services.store.kv_get("remote_devices", {}))
    assert key.split(".", 1)[1] not in json.dumps([a for a in services.store.audit_log(200)])
    assert phone.get("/api/remote/actions", headers=auth).status_code == 200


def test_the_pairing_code_is_owner_only_single_use_and_expires(rig):
    client, services, h, *_ = rig
    assert phone_client(services).post("/api/v3/remote/pairing-code").status_code in (401, 403)
    code = client.post("/api/v3/remote/pairing-code", headers=h).json()["code"]
    phone = phone_client(services)
    assert phone.post("/api/remote/pair", json={"code": code, "name": "A"}).status_code == 200
    assert phone.post("/api/remote/pair", json={"code": code, "name": "B"}).status_code == 403      # used up
    code = client.post("/api/v3/remote/pairing-code", headers=h).json()["code"]
    real = services.remote.clock
    services.remote.clock = lambda: real() + 301
    assert phone.post("/api/remote/pair", json={"code": code, "name": "C"}).status_code == 403      # too old
    services.remote.clock = real


def test_guessing_the_pairing_code_kills_it(rig):
    client, services, h, *_ = rig
    code = client.post("/api/v3/remote/pairing-code", headers=h).json()["code"]
    phone = phone_client(services)
    for _ in range(rc.CODE_TRIES):
        assert phone.post("/api/remote/pair", json={"code": "WRONG000", "name": "x"}).status_code == 403
    assert phone.post("/api/remote/pair", json={"code": code, "name": "x"}).status_code == 403      # the real one is dead


def test_pairing_only_over_the_private_link(rig):
    client, services, h, *_ = rig
    code = client.post("/api/v3/remote/pairing-code", headers=h).json()["code"]
    outside = phone_client(services, ip="203.0.113.9")
    assert outside.post("/api/remote/pair", json={"code": code, "name": "x"}).status_code == 403
    assert services.settings.remote_control and not services.store.kv_get("remote_devices", {})


def test_no_key_a_wrong_key_and_a_revoked_key_are_refused(rig):
    phone, auth, got = pair(rig)
    client, services, h, *_ = rig
    assert phone.get("/api/remote/actions").status_code == 401
    assert phone.get("/api/remote/actions", headers={"Authorization": "Bearer nope.nope"}).status_code == 401
    assert phone.get("/api/remote/actions", headers={"Authorization": auth["Authorization"] + "x"}).status_code == 401
    assert run(phone, auth, "battery").status_code == 200
    assert client.post(f"/api/v3/remote/devices/{got['device_id']}/revoke", headers=h).status_code == 200
    assert phone.get("/api/remote/actions", headers=auth).status_code == 401
    assert services.laptop.calls == []


def test_a_correct_key_from_outside_the_private_link_is_refused(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    outside = phone_client(services, ip="203.0.113.9")
    assert outside.get("/api/remote/actions", headers=auth).status_code == 403
    assert run(outside, auth, "battery").status_code == 403
    proxied = phone_client(services)
    assert proxied.get("/api/remote/actions", headers={**auth, "X-Forwarded-For": "8.8.8.8"}).status_code == 403


def test_the_owners_browser_login_does_not_open_the_phone_api(rig):
    client, services, h, *_ = rig                                    # logged in with a session cookie
    assert client.get("/api/remote/actions").status_code == 401
    assert client.post("/api/remote/run", json={"action": "battery", "params": {}}).status_code == 401


def test_too_many_wrong_keys_are_slowed_down(rig):
    client, services, *_ = rig
    phone = phone_client(services)
    codes = [phone.get("/api/remote/actions", headers={"Authorization": "Bearer a.b"}).status_code for _ in range(15)]
    assert codes[0] == 401 and 429 in codes


def test_with_the_switch_off_or_stop_all_nothing_works(rig):
    phone, auth, _ = pair(rig)
    client, services, h, *_ = rig
    services.settings.remote_control = False
    assert run(phone, auth, "battery").status_code == 403
    services.settings.remote_control = True
    services.killswitch.trigger("test")
    assert run(phone, auth, "battery").status_code == 423
    assert services.laptop.calls == []


def test_the_phone_needs_access_from_other_devices_to_reach_the_laptop(rig):
    client, services, *_ = rig
    services.settings.allow_lan = False                 # the existing host check: only this laptop's own names
    assert phone_client(services).get("/api/remote/actions").status_code == 400


def test_the_features_switch_exists_and_is_off_by_default():
    assert BY_KEY["remote_control"].label == "Control the laptop from the phone"
    assert on(Settings(), "remote_control") is False


# -- item 1: the list of actions ---------------------------------------------------------------------------------------------
DANGEROUS = {"click", "type", "press_keys", "switch_app"}
SAMPLES = {"battery": {}, "wifi_status": {}, "bluetooth": {"want": "status"}, "volume": {"level": 30}, "mute": {},
           "brightness": {"level": 50}, "dark_mode": {"mode": "on"}, "do_not_disturb": {"on": True},
           "apps_running": {}, "app_open": {"name": "calculator"}, "app_close": {"name": "calculator"}, "lock": {},
           "screenshot": {}, "read_controls": {}, "list_windows": {},
           "click": {"x": 10, "y": 20}, "type": {"text": "hello"}, "press_keys": {"keys": "ctrl+c"},
           "switch_app": {"app": "notepad"}}


def test_the_list_has_the_laptop_actions_and_the_6_4a_ones_with_their_risk(rig):
    phone, auth, _ = pair(rig)
    listed = {a["id"]: a for a in phone.get("/api/remote/actions", headers=auth).json()["actions"]}
    assert set(listed) == set(SAMPLES)
    assert {k for k, a in listed.items() if a["needs_laptop_yes"]} == DANGEROUS
    assert all(a["title"] and a["asks_on_phone"] is True and isinstance(a["params"], dict) for a in listed.values())
    for gone in ("shutdown", "restart", "install_app", "wifi_connect_new", "power"):
        assert gone not in listed


@pytest.mark.parametrize("action", sorted(SAMPLES))
def test_every_listed_action_really_runs_after_the_yes(rig, action):
    phone, auth, _ = pair(rig)
    client, services, h, pc, notes = rig
    started = run(phone, auth, action, **SAMPLES[action]).json()
    assert started["state"] == "waiting_phone" and started["confirm"]
    assert services.laptop.calls == [] and pc["input"] == [] and pc["read"] == []            # asking runs nothing
    done = confirm(phone, auth, started).json()
    if action in DANGEROUS:
        assert done["state"] == "waiting_laptop"
        assert pc["input"] == []
        item = next(a for a in services.store.big_approvals() if a.kind == "remote_action")
        services.approvals.resolve(item.id, approved=True)
        done = phone.get(f"/api/remote/requests/{started['id']}", headers=auth).json()
        assert pc["input"], "the mouse/keyboard action ran only after the laptop's yes"
    assert done["state"] == "done", done
    assert services.laptop.calls or pc["read"] or pc["input"]


def test_bad_actions_and_bad_values_are_refused_before_anything_is_asked(rig):
    phone, auth, _ = pair(rig)
    client, services, h, pc, notes = rig
    assert run(phone, auth, "format_disk").status_code == 404
    for action, params in (("volume", {"level": 101}), ("volume", {"level": "loud"}), ("volume", {}),
                           ("app_open", {"name": "calc; del *"}), ("bluetooth", {"want": "maybe"}),
                           ("dark_mode", {"mode": "grey"}), ("click", {"x": "a", "y": 1}),
                           ("type", {"text": ""}), ("type", {"text": "token sk-ant-api03-" + "x" * 40}),
                           ("press_keys", {"keys": "ctrl+alt+explode"}), ("battery", {"extra": 1})):
        r = run(phone, auth, action, **params)
        assert r.status_code == 422, (action, params, r.text)
    assert not rc_requests(services) and services.laptop.calls == []


def rc_requests(services):
    return list(services.remote._requests)


# -- item 3: every action asks on the phone ----------------------------------------------------------------------------------
def test_the_action_runs_only_after_a_separate_yes_and_only_once(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    started = run(phone, auth, "volume", level=30).json()
    assert services.laptop.calls == []
    first = confirm(phone, auth, started)
    assert first.status_code == 200 and first.json()["state"] == "done"
    assert services.laptop.calls == [("volume", (30,), {})]
    assert confirm(phone, auth, started).status_code == 409                    # a yes works once
    assert len(services.laptop.calls) == 1
    assert not [a for a in services.store.big_approvals() if a.kind == "remote_action"]       # no laptop yes for this one


def test_the_yes_is_tied_to_the_request_and_cannot_change_the_values(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    one = run(phone, auth, "volume", level=30).json()
    two = run(phone, auth, "volume", level=90).json()
    bad = phone.post(f"/api/remote/requests/{one['id']}/confirm", json={"confirm": two["confirm"]}, headers=auth)
    assert bad.status_code == 403 and services.laptop.calls == []
    sneaky = phone.post(f"/api/remote/requests/{one['id']}/confirm",
                        json={"confirm": one["confirm"], "params": {"level": 100}, "action": "lock"}, headers=auth)
    assert sneaky.status_code == 200 and services.laptop.calls == [("volume", (30,), {})]


def test_a_yes_that_is_too_old_does_nothing(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    started = run(phone, auth, "mute").json()
    real = services.remote.clock
    services.remote.clock = lambda: real() + rc.CONFIRM_TTL + 1
    assert confirm(phone, auth, started).status_code == 410
    services.remote.clock = real
    assert services.laptop.calls == []


def test_one_phone_cannot_see_or_confirm_anothers_request(rig):
    phone, auth, _ = pair(rig)
    other, other_auth, _ = pair(rig, name="Another phone")
    client, services, *_ = rig
    started = run(phone, auth, "mute").json()
    assert other.get(f"/api/remote/requests/{started['id']}", headers=other_auth).status_code == 404
    assert confirm(other, other_auth, started).status_code == 404
    assert services.laptop.calls == []


def test_cancelling_stops_the_request(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    started = run(phone, auth, "mute").json()
    assert phone.post(f"/api/remote/requests/{started['id']}/cancel", headers=auth).json()["state"] == "cancelled"
    assert confirm(phone, auth, started).status_code == 409 and services.laptop.calls == []


def test_stop_all_between_the_asking_and_the_yes_stops_it(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    started = run(phone, auth, "mute").json()
    services.killswitch.trigger("test")
    assert confirm(phone, auth, started).status_code == 423 and services.laptop.calls == []


# -- item 3: the 6.4a mouse and keyboard also show on the laptop ---------------------------------------------------------
def test_typing_waits_for_a_yes_on_the_laptop_too_and_shows_what_it_will_do(rig):
    phone, auth, _ = pair(rig, name="Ravi's phone")
    client, services, h, pc, notes = rig
    started = run(phone, auth, "type", text="hello world").json()
    done = confirm(phone, auth, started).json()
    assert done["state"] == "waiting_laptop" and pc["input"] == []
    item = next(a for a in services.store.big_approvals() if a.kind == "remote_action")
    assert "phone" in item.summary.lower() and "hello world" in item.summary and "Ravi's phone" in item.summary
    assert any("phone" in text.lower() for _, text in notes)                    # a notification on the laptop
    assert phone.get(f"/api/remote/requests/{started['id']}", headers=auth).json()["state"] == "waiting_laptop"
    services.approvals.resolve(item.id, approved=True)
    final = phone.get(f"/api/remote/requests/{started['id']}", headers=auth).json()
    assert final["state"] == "done" and pc["input"] == [{"action": "type", "text": "hello world",
                                                         "data_dir": pc["input"][0]["data_dir"]}]


def test_without_the_laptops_yes_a_click_never_happens(rig):
    phone, auth, _ = pair(rig)
    client, services, h, pc, notes = rig
    started = run(phone, auth, "click", x=5, y=6).json()
    confirm(phone, auth, started)
    time.sleep(0.05)
    assert pc["input"] == []
    assert phone.get(f"/api/remote/requests/{started['id']}", headers=auth).json()["state"] == "waiting_laptop"


def test_a_laptop_yes_that_comes_too_late_or_after_a_restart_does_nothing(rig):
    phone, auth, _ = pair(rig)
    client, services, h, pc, notes = rig
    started = run(phone, auth, "press_keys", keys="ctrl+c").json()
    confirm(phone, auth, started)
    item = next(a for a in services.store.big_approvals() if a.kind == "remote_action")
    real = services.remote.clock
    services.remote.clock = lambda: real() + rc.LAPTOP_TTL + 1
    msg = services.approvals.resolve(item.id, approved=True)
    services.remote.clock = real
    assert pc["input"] == [] and "ask again" in msg.lower()
    # after a restart the pending requests are gone: the old approval can't run anything
    started = run(phone, auth, "press_keys", keys="ctrl+v").json()
    confirm(phone, auth, started)
    item = [a for a in services.store.big_approvals() if a.kind == "remote_action"][-1]
    services.remote._requests.clear()
    assert "ask again" in services.approvals.resolve(item.id, approved=True).lower() and pc["input"] == []


def test_stop_all_blocks_the_laptops_yes_too(rig):
    phone, auth, _ = pair(rig)
    client, services, h, pc, notes = rig
    started = run(phone, auth, "type", text="x y").json()
    confirm(phone, auth, started)
    item = next(a for a in services.store.big_approvals() if a.kind == "remote_action")
    services.killswitch.trigger("test")
    services.approvals.resolve(item.id, approved=True)
    assert pc["input"] == []


def test_cancelling_a_waiting_request_makes_a_later_laptop_yes_do_nothing(rig):
    phone, auth, _ = pair(rig)
    client, services, h, pc, notes = rig
    started = run(phone, auth, "type", text="a b").json()
    confirm(phone, auth, started)
    phone.post(f"/api/remote/requests/{started['id']}/cancel", headers=auth)
    item = next(a for a in services.store.big_approvals() if a.kind == "remote_action")
    services.approvals.resolve(item.id, approved=True)
    assert pc["input"] == []


def test_a_failing_action_is_reported_plainly(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    from prometheus.laptop import LaptopError

    def boom(*a, **k):
        raise LaptopError("This only works on Windows.")

    services.laptop.volume = boom
    started = run(phone, auth, "volume", level=10).json()
    done = confirm(phone, auth, started).json()
    assert done["state"] == "failed" and "only works on Windows" in done["error"]


# -- item 4: the activity log ------------------------------------------------------------------------------------------------------
def test_every_step_is_in_the_activity_log_without_secrets(rig):
    phone, auth, got = pair(rig, name="Ravi's phone")
    client, services, h, pc, notes = rig
    started = run(phone, auth, "type", text="my diary entry").json()
    confirm(phone, auth, started)
    item = next(a for a in services.store.big_approvals() if a.kind == "remote_action")
    services.approvals.resolve(item.id, approved=True)
    done = run(phone, auth, "volume", level=20).json()
    confirm(phone, auth, done)
    phone.post(f"/api/remote/requests/{run(phone, auth, 'mute').json()['id']}/cancel", headers=auth)
    phone_client(services, "203.0.113.9").get("/api/remote/actions", headers=auth)
    phone.get("/api/remote/actions", headers={"Authorization": "Bearer bad.key"})
    client.post(f"/api/v3/remote/devices/{got['device_id']}/revoke", headers=h)
    seen = set(audit_actions(services))
    assert {"remote_pair", "remote_proposed", "remote_confirmed", "remote_laptop_yes", "remote_ran", "remote_cancelled",
            "remote_refused", "remote_revoke", "remote_code"} <= seen
    log = json.dumps(services.store.audit_log(200))
    mine = json.dumps([a for a in services.store.audit_log(200) if a["action"].startswith("remote_")])
    assert "my diary entry" not in mine                                    # the remote steps never log typed text
    assert "type" in mine and "Ravi's phone" in mine and "volume" in mine
    # (the Approvals page must show the text so the owner can judge it, and Approvals' own "requested" line keeps it)
    key = auth["Authorization"].split(" ", 1)[1]
    assert key not in log and key.split(".", 1)[1] not in log
    why = [a["detail"].get("why") for a in services.store.audit_log(200) if a["action"] == "remote_refused"]
    assert "not the private link" in why and "unknown key" in why


def test_a_failed_action_is_logged_as_failed(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig

    def boom(*a, **k):
        raise RuntimeError("secret internals /etc/passwd")

    services.laptop.mute = boom
    confirm(phone, auth, run(phone, auth, "mute").json())
    last = next(a for a in services.store.audit_log(50) if a["action"] == "remote_failed")
    assert last["detail"]["action"] == "mute" and "passwd" not in json.dumps(last)


def test_the_activity_page_shows_the_remote_steps_in_plain_words(rig):
    phone, auth, _ = pair(rig)
    client, services, *_ = rig
    confirm(phone, auth, run(phone, auth, "volume", level=20).json())
    page = client.get("/v3/system?tab=activity").text
    assert "Phone remote" in page and "volume" in page


# -- the owner's side: the Devices page ------------------------------------------------------------------------------------
def test_the_devices_page_shows_pairing_and_the_paired_phones(rig):
    phone, auth, got = pair(rig, name="Ravi's phone")
    client, services, h, *_ = rig
    page = client.get("/v3/system?tab=devices").text
    assert "Control the laptop from your phone" in page and "Ravi&#39;s phone" in page
    assert "/api/v3/remote/pairing-code" in page and f"/api/v3/remote/devices/{got['device_id']}/revoke" in page
    services.settings.remote_control = False
    off = client.get("/v3/system?tab=devices").text
    assert "Settings → Features" in off and "/api/v3/remote/pairing-code" not in off


def test_at_most_five_phones_can_be_paired(rig):
    for i in range(rc.MAX_DEVICES):
        pair(rig, name=f"phone {i}")
    client, services, h, *_ = rig
    code = client.post("/api/v3/remote/pairing-code", headers=h).json()["code"]
    assert phone_client(services).post("/api/remote/pair", json={"code": code, "name": "one more"}).status_code == 409


def test_the_pairing_code_needs_the_switch_on(rig):
    client, services, h, *_ = rig
    services.settings.remote_control = False
    assert client.post("/api/v3/remote/pairing-code", headers=h).status_code == 409
