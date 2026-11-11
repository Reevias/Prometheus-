import pytest

import prometheus.agents.phone_agent as pa
from prometheus.connectors.android import Phone, PhoneError, type_escape
from prometheus.quick import understand

UI = """<?xml version='1.0' encoding='UTF-8' standalone='yes' ?><hierarchy rotation="0">
<node text="Accept" class="android.widget.Button" clickable="true" bounds="[100,1800][500,1900]" />
<node text="" content-desc="Send" class="android.widget.ImageButton" clickable="true" bounds="[900,2000][1000,2100]" />
<node text="Pay ₹499" class="android.widget.Button" clickable="true" bounds="[100,2200][900,2300]" />
</hierarchy>"""


class FakeAdb:
    def __init__(self):
        self.calls = []

    def __call__(self, args, timeout=30):
        self.calls.append(args)
        cmd = args[-1] if args else ""
        if args[1] == "pair":
            return 0, b"Successfully paired to 100.64.1.2:37123"
        if args[1] == "connect":
            return 0, b"connected to 100.64.1.2:41234"
        if "exec-out" in args:
            return 0, b"\x89PNG fake"
        if "pm list packages" in cmd:
            return 0, b"package:com.whatsapp\npackage:com.spotify.music\npackage:com.termux\n"
        if "cat /sdcard/prometheus-ui.xml" in cmd:
            return 0, UI.encode()
        if "content query" in cmd:
            return 0, b"Row: 0 display_name=Ravi Kumar, data1=+91 98100 11111\nRow: 1 display_name=Mom, data1=98100 22222\n"
        if "getprop" in cmd:
            return 0, b"Pixel 8\n"
        return 0, b""


@pytest.fixture
def adb(monkeypatch):
    fake = FakeAdb()
    monkeypatch.setattr(pa, "phone", lambda: Phone("100.64.1.2:41234", adb="adb", run=fake))
    return fake


def shells(fake):
    return [c[-1] for c in fake.calls if "shell" in c]


def test_pair_connect_and_checks():
    fake = FakeAdb()
    p = Phone(adb="adb", run=fake)
    assert p.pair("100.64.1.2:37123", "123456") == "Paired."
    assert "Pixel 8" in p.connect("100.64.1.2:41234")
    with pytest.raises(PhoneError):
        p.pair("100.64.1.2:37123; rm -rf /", "123456")
    with pytest.raises(PhoneError):
        Phone(run=fake).open_app("spotify")                              # not paired yet


def test_free_actions(adb, make_ctx):
    ctx = make_ctx()
    agent = pa.PhoneAgent()
    assert agent.run("x", {"action": "open_app", "app": "Spotify"}, ctx).status == "done"
    assert "monkey -p com.spotify.music" in shells(adb)[-1]
    assert agent.run("x", {"action": "open_app", "app": "termux"}, ctx).status == "failed"     # never terminals
    agent.run("x", {"action": "setting", "what": "wifi", "value": "off"}, ctx)
    assert shells(adb)[-1] == "cmd wifi set-wifi-enabled disabled"
    agent.run("x", {"action": "alarm", "time": "6:45 pm", "label": "Gym; rm"}, ctx)
    assert "HOUR 18" in shells(adb)[-1] and "MINUTES 45" in shells(adb)[-1] and ";" not in shells(adb)[-1]
    assert agent.run("x", {"action": "setting", "what": "volume", "value": "99"}, ctx).status == "failed"


def test_taps_typing_messages_calls_are_asked(adb, make_ctx):
    ctx = make_ctx()
    ctx.approvals.register("phone_action", pa.action_handler)
    agent = pa.PhoneAgent()
    res = agent.run("x", {"action": "tap", "target": "accept"}, ctx)
    assert res.status == "waiting_approval" and not any("input tap" in s for s in shells(adb))
    ask = ctx.store.asks_for_chat(ctx.chat_id())[-1]
    ctx.approvals.resolve(ask.id, approved=True)
    assert "input tap 300 1850" in shells(adb)[-1]                          # the exact spot Android reported
    pay = agent.run("x", {"action": "tap", "target": "pay"}, ctx)
    assert pay.status == "waiting_approval" and any(a.kind == "phone_action" for a in ctx.store.big_approvals())
    msg = agent.run("x", {"action": "message", "to": "Ravi", "text": "Running 10 min late", "app": "whatsapp"}, ctx)
    ask = ctx.store.asks_for_chat(ctx.chat_id())[-1]
    assert msg.status == "waiting_approval" and "Ravi Kumar" in ask.summary and "Running 10 min late" in ask.summary
    import prometheus.connectors.android as an

    an.time.sleep = lambda s: None
    out = ctx.approvals.resolve(ask.id, approved=True)
    assert "wa.me/919810011111?text=Running%2010%20min%20late" in " ".join(shells(adb)) and "Sent to Ravi" in out
    call = agent.run("x", {"action": "call", "to": "Mom"}, ctx)
    assert call.status == "waiting_approval" and not any("CALL" in s for s in shells(adb))


def test_typing_is_escaped_and_never_into_password_boxes():
    assert type_escape("hi there; rm -rf $HOME") == r"hi%sthere\;%srm%s-rf%s\$HOME"
    fake = FakeAdb()
    ui = UI.replace('content-desc="Send"', 'content-desc="Send" password="true"')
    fake_ui = lambda args, timeout=30: (0, ui.encode()) if "cat /sdcard" in args[-1] else fake(args, timeout)
    with pytest.raises(PhoneError):
        Phone("x:1", adb="adb", run=fake_ui).type_text("secret")


def test_phrases():
    assert understand("open spotify on my phone").steps[0]["inputs"] == {"action": "open_app", "app": "spotify"}
    assert understand("turn off wifi on my phone").steps[0]["inputs"]["value"] == "off"
    assert understand("set an alarm for 7am on my phone").steps[0]["inputs"]["time"] == "7am"
    assert understand("what's on my phone screen").steps[0]["inputs"]["action"] == "look"
    assert understand("open spotify").steps[0]["agent"] == "system"              # laptop, as before
