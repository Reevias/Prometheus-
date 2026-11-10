"""v1.3: System agent (Wi-Fi, Bluetooth, location, battery) and the Antigravity agent."""
import json
from types import SimpleNamespace

import pytest

from conftest import login

from prometheus.agents.antigravity import Antigravity, run_antigravity_job
from prometheus.agents.system import SystemAgent, wifi_password_handler
from prometheus.laptop import Laptop, LaptopError
from prometheus.safety.permissions import Decision

INTERFACES = """
There is 1 interface on the system:

    Name                   : Wi-Fi
    Description            : Intel(R) Wi-Fi 6 AX201 160MHz
    State                  : connected
    SSID                   : HomeNet
    BSSID                  : aa:bb:cc:dd:ee:ff
    Signal                 : 86%
"""
NETWORKS = """
SSID 1 : HomeNet
    Network type            : Infrastructure
    Authentication          : WPA2-Personal
    Encryption              : CCMP
         Signal             : 86%

SSID 2 : Cafe Free WiFi
    Network type            : Infrastructure
    Authentication          : Open
    Encryption              : None
         Signal             : 40%

SSID 3 : Office-5G
    Authentication          : WPA2-Personal
         Signal             : 71%
"""
PROFILES = """
User profiles
-------------
    All User Profile     : HomeNet
    All User Profile     : Phone Hotspot
"""


class FakeRun:
    def __init__(self, extra=None):
        self.calls = []
        self.files = {}
        self.extra = extra or {}

    def __call__(self, argv, timeout=20):
        self.calls.append(list(argv))
        joined = " ".join(argv)
        for needle, reply in self.extra.items():
            if needle in joined:
                return reply
        if "show interfaces" in joined:
            return 0, INTERFACES
        if "show networks" in joined:
            return 0, NETWORKS
        if "show profiles" in joined:
            return 0, PROFILES
        if "add profile" in joined:
            path = next(a for a in argv if a.startswith("filename=")).split("=", 1)[1]
            self.files[path] = open(path, encoding="utf-8").read()
            return 0, "Profile added"
        return 0, "ok"


def laptop(run=None):
    return Laptop(run=run or FakeRun(), platform="win32")


def test_wifi_status_and_list():
    lp = laptop()
    assert lp.wifi_status() == {"connected": True, "ssid": "HomeNet", "signal": "86%"}
    nets = {n["ssid"]: n for n in lp.wifi_networks()}
    assert nets["Cafe Free WiFi"]["open"] is True and nets["Office-5G"]["open"] is False
    assert nets["HomeNet"]["signal"] == "86%" and lp.wifi_known() == ["HomeNet", "Phone Hotspot"]


def test_commands_are_fixed_lists_never_a_shell_string():
    run = FakeRun()
    lp = laptop(run)
    lp.wifi_connect_known("Phone Hotspot")
    assert run.calls[-1] == ["netsh", "wlan", "connect", "name=Phone Hotspot", "ssid=Phone Hotspot"]


@pytest.mark.parametrize("ssid", ['evil" & calc.exe', "a\nb", "", "x" * 33, "name=other ssid=x\x00"])
def test_bad_network_names_are_refused(ssid):
    run = FakeRun()
    with pytest.raises(LaptopError):
        laptop(run).wifi_connect_known(ssid)
    assert run.calls == []


def test_new_network_profile_holds_password_then_file_is_removed(tmp_path):
    run = FakeRun()
    lp = laptop(run)
    lp.wifi_connect_new("Office-5G", "s3cret<&>pass", workdir=tmp_path)
    [(path, xml)] = run.files.items()
    assert "<name>Office-5G</name>" in xml and "s3cret&lt;&amp;&gt;pass" in xml and "WPA2PSK" in xml
    assert list(tmp_path.iterdir()) == []                      # the file with the password is gone
    assert run.calls[-1][:3] == ["netsh", "wlan", "connect"]
    with pytest.raises(LaptopError):
        lp.wifi_connect_new("Office-5G", "short", workdir=tmp_path)


def test_not_windows_gives_a_clear_message():
    with pytest.raises(LaptopError, match="Windows"):
        Laptop(run=FakeRun(), platform="linux").wifi_status()


def test_bluetooth_location_battery_use_fixed_scripts():
    run = FakeRun({"RadioState": (0, "On"), "GeoCoordinateWatcher": (0, "18.5204,73.8567,65"),
                   "Win32_Battery": (0, '{"EstimatedChargeRemaining":77,"BatteryStatus":2}')})
    lp = laptop(run)
    assert lp.bluetooth("status") == "On"
    assert lp.location() == {"latitude": 18.5204, "longitude": 73.8567, "accuracy_m": 65.0}
    assert lp.battery() == {"percent": 77, "charging": True}
    for call in run.calls:
        assert call[0] == "powershell" and "-NonInteractive" in call
    with pytest.raises(LaptopError):
        lp.bluetooth("explode")


def agent(run=None):
    return SystemAgent(laptop=laptop(run))


def test_system_agent_is_allowed_and_private(make_ctx):
    ctx = make_ctx()
    assert ctx.permissions.check("system", "system_read") is Decision.ALLOW
    assert ctx.permissions.check("system", "system_change") is Decision.ALLOW
    assert SystemAgent.private_outputs is True


def test_switch_to_known_wifi_runs_straight_away(make_ctx):
    run = FakeRun()
    ctx = make_ctx()
    result = agent(run).run("switch", {"action": "wifi_connect", "ssid": "Phone Hotspot"}, ctx)
    assert result.status == "done" and ctx.store.approvals() == []
    assert ["netsh", "wlan", "connect", "name=Phone Hotspot", "ssid=Phone Hotspot"] in run.calls
    assert any(e["action"] == "wifi_connect" for e in ctx.store.audit_log())


def test_unknown_secured_wifi_asks_for_password_in_the_app(make_ctx, tmp_path):
    run = FakeRun()
    ctx = make_ctx()
    result = agent(run).run("switch", {"action": "wifi_connect", "ssid": "Office-5G"}, ctx)
    assert result.status == "waiting_approval" and "password" in result.summary.lower()
    [a] = ctx.store.approvals()
    assert a.kind == "wifi_password" and a.payload["ssid"] == "Office-5G" and a.payload["_secret_label"]
    msg = wifi_password_handler(a.payload, "my-wifi-pass-1", laptop(run), tmp_path)
    assert "Office-5G" in msg
    everything = json.dumps(ctx.store.audit_log()) + json.dumps([x.payload for x in ctx.store.approvals("pending")])
    assert "my-wifi-pass-1" not in everything


def test_open_wifi_connects_without_password(make_ctx):
    run = FakeRun()
    ctx = make_ctx()
    result = agent(run).run("s", {"action": "wifi_connect", "ssid": "Cafe Free WiFi"}, ctx)
    assert result.status == "done" and ctx.store.approvals() == []


def test_unknown_action_and_missing_network(make_ctx):
    ctx = make_ctx()
    assert agent().run("s", {"action": "format_disk"}, ctx).status == "failed"
    assert agent().run("s", {"action": "wifi_connect", "ssid": "NotHere"}, ctx).status == "failed"


def test_secret_goes_to_handler_but_is_never_stored(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    got = []
    services.approvals.register("wifi_password", lambda payload, secret: got.append(secret) or "Connected",
                                needs_secret=True)
    aid = services.approvals.request("wifi_password", "Wi-Fi password needed for Office-5G", {"ssid": "Office-5G"},
                                     secret_label="Wi-Fi password")
    page = client.get("/approvals").text
    assert 'type="password"' in page and "Wi-Fi password" in page
    resp = client.post(f"/approvals/{aid}", data={"decision": "approve", "secret": "my-wifi-pass-1"}, headers=h)
    assert resp.status_code == 200 and got == ["my-wifi-pass-1"]
    assert "my-wifi-pass-1" not in resp.text
    assert "my-wifi-pass-1" not in json.dumps(services.store.audit_log())
    assert "my-wifi-pass-1" not in json.dumps(services.store.approval(aid).payload)


# -- Antigravity -----------------------------------------------------------------
class FakeGenai:
    def __init__(self, status="completed", text="Report written."):
        self.created = []
        self.status, self.text = status, text
        self.interactions = SimpleNamespace(create=self._create, get=self._get)

    def _create(self, **kw):
        self.created.append(kw)
        return SimpleNamespace(id="int_1", status="in_progress", output_text="", usage=None)

    def _get(self, id):
        return SimpleNamespace(id=id, status=self.status, output_text=self.text,
                               usage=SimpleNamespace(total_tokens=12345))


def test_antigravity_always_asks_first_and_never_takes_private_data(make_ctx):
    ctx = make_ctx()
    assert Antigravity.accepts_private is False
    result = Antigravity().run("job", {"task": "Summarise the top Hacker News stories"}, ctx)
    assert result.status == "waiting_approval"
    [a] = ctx.store.approvals()
    assert a.kind == "antigravity_job" and a.payload["task"].startswith("Summarise")
    assert "Google" in a.summary


def test_antigravity_job_request_shape_and_result(tmp_home):
    client = FakeGenai()
    out = run_antigravity_job({"task": "Do the thing", "model": "gemini-3.8-flash", "max_tokens": 50000}, client,
                              sleep=lambda s: None)
    kw = client.created[0]
    assert kw["agent"] == "antigravity-preview-09-2026" and kw["input"] == "Do the thing"
    assert kw["environment"] == "remote" and kw["background"] is True
    assert kw["agent_config"] == {"type": "antigravity", "model": "gemini-3.8-flash", "max_total_tokens": 50000}
    assert out["text"] == "Report written." and out["status"] == "completed" and out["tokens"] == 12345


def test_antigravity_incomplete_or_failed_is_reported(tmp_home):
    out = run_antigravity_job({"task": "x", "model": "m", "max_tokens": 1000}, FakeGenai(status="incomplete", text="part"),
                              sleep=lambda s: None)
    assert out["status"] == "incomplete" and "limit" in out["note"].lower()
    out = run_antigravity_job({"task": "x", "model": "m", "max_tokens": 1000}, FakeGenai(status="failed", text=""),
                              sleep=lambda s: None)
    assert out["status"] == "failed"
