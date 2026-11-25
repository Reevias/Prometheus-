"""M6 step 6.4a: Control PC (screen, mouse, keyboard, Windows UI Automation, safety, stop-all)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from PIL import Image


def _make_fake_backend(windows=None, fg_idx=0, vscreen=(0, 0, 1920, 1080)):
    """In-memory fake Win32 backend so Item 1 logic can be tested without touching the desktop."""
    from prometheus.pc_control.win32 import WindowInfo

    wins = list(windows or [
        WindowInfo(hwnd=101, title="Notes - Notepad", process="notepad.exe", pid=1001, rect=(10, 10, 810, 610)),
        WindowInfo(hwnd=102, title="Calculator", process="CalculatorApp.exe", pid=1002, rect=(100, 100, 500, 700)),
        WindowInfo(hwnd=103, title="Windows PowerShell", process="powershell.exe", pid=1003, rect=(0, 0, 900, 600)),
    ])
    state = {"fg": wins[fg_idx], "events": [], "cursor": (0, 0)}

    class FakeWin32:
        def virtual_screen_rect(self):
            return vscreen

        def foreground_window(self):
            return state["fg"]

        def list_windows(self):
            return list(wins)

        def set_foreground(self, hwnd: int) -> bool:
            for w in wins:
                if w.hwnd == hwnd:
                    state["fg"] = w
                    state["events"].append(("switch", hwnd, w.title))
                    return True
            return False

        def click(self, x: int, y: int, button: str = "left", clicks: int = 1, stop_check=None):
            if stop_check and stop_check():
                raise InterruptedError("stopped")
            state["cursor"] = (x, y)
            state["events"].append(("click", x, y, button, clicks))

        def type_text(self, text: str, stop_check=None):
            for ch in text:
                if stop_check and stop_check():
                    raise InterruptedError("stopped")
                state["events"].append(("char", ch))

        def press_keys(self, keys: tuple[int, ...], stop_check=None):
            if stop_check and stop_check():
                raise InterruptedError("stopped")
            state["events"].append(("keys", keys))

    return FakeWin32(), state


# -- Item 1: prometheus/pc_control/ core capabilities --------------------------------------------------------------
def test_item1_screenshot_captures_bounded_png_in_private_dir(tmp_path):
    from prometheus.pc_control.controller import PCController

    raw = Image.new("RGB", (2400, 1200), color=(20, 40, 60))
    ctrl = PCController(data_dir=tmp_path, grabber=lambda: raw)
    shot = ctrl.screenshot()
    assert shot.path.exists()
    assert shot.path.parent == tmp_path / "pc_control"
    assert shot.path.suffix == ".png"
    assert shot.width == 1600 and shot.height == 800
    with Image.open(shot.path) as saved:
        assert saved.size == (1600, 800)


def test_item1_read_controls_parses_uia_and_redacts_password_values(tmp_path):
    from prometheus.pc_control.controller import PCController

    uia_payload = json.dumps({
        "window": "Sign in",
        "focused": {"name": "Username", "control_type": "Edit", "automation_id": "user", "is_password": False},
        "controls": [
            {"name": "Username", "control_type": "Edit", "automation_id": "user", "rect": [10, 20, 210, 50],
             "focused": True, "enabled": True, "is_password": False, "value": "alice"},
            {"name": "Password", "control_type": "Edit", "automation_id": "pwd", "rect": [10, 60, 210, 90],
             "focused": False, "enabled": True, "is_password": True, "value": "should_never_leak"},
            {"name": "Submit", "control_type": "Button", "automation_id": "go", "rect": [10, 100, 110, 130],
             "focused": False, "enabled": True, "is_password": False, "value": ""},
        ],
    })
    ctrl = PCController(data_dir=tmp_path, ps_runner=lambda script, stop_check=None: uia_payload)
    res = ctrl.read_controls()
    assert res["window"] == "Sign in"
    assert len(res["controls"]) == 3
    pwd = [c for c in res["controls"] if c.name == "Password"][0]
    assert pwd.is_password is True
    assert "should_never_leak" not in pwd.value
    assert pwd.value == ""


def test_item1_click_checks_bounds_and_dispatches_to_backend(tmp_path):
    from prometheus.pc_control.controller import PCController, PCControlError

    backend, state = _make_fake_backend()
    ctrl = PCController(data_dir=tmp_path, backend=backend)
    out = ctrl.click(250, 300, button="left", clicks=1)
    assert out["x"] == 250 and out["y"] == 300
    assert state["cursor"] == (250, 300)
    assert ("click", 250, 300, "left", 1) in state["events"]

    with pytest.raises(PCControlError, match="bounds"):
        ctrl.click(5000, 5000)


def test_item1_type_text_and_press_keys_dispatch_and_block_terminals(tmp_path):
    from prometheus.pc_control.controller import PCController, PCControlError

    backend, state = _make_fake_backend()
    ctrl = PCController(
        data_dir=tmp_path,
        backend=backend,
        password_checker=lambda stop_check=None: (False, ""),
    )
    ctrl.type_text("hello")
    assert [e for e in state["events"] if e[0] == "char"] == [("char", c) for c in "hello"]

    ctrl.press_keys("ctrl+c")
    key_events = [e for e in state["events"] if e[0] == "keys"]
    assert len(key_events) == 1 and len(key_events[0][1]) == 2

    # Switch active foreground to PowerShell (by fake backend state) -> must refuse typing/clicking/keys
    backend.set_foreground(103)
    with pytest.raises(PCControlError, match="terminal|blocked"):
        ctrl.type_text("dir")
    with pytest.raises(PCControlError, match="terminal|blocked"):
        ctrl.press_keys("enter")
    with pytest.raises(PCControlError, match="terminal|blocked"):
        ctrl.click(50, 50)


def test_item1_switch_app_switches_to_allowed_window_and_refuses_terminals(tmp_path):
    from prometheus.pc_control.controller import PCController, PCControlError

    backend, state = _make_fake_backend()
    ctrl = PCController(data_dir=tmp_path, backend=backend)

    wins = ctrl.list_windows()
    assert any(w.title == "Calculator" for w in wins)

    switched = ctrl.switch_app("Calculator")
    assert switched.title == "Calculator"
    assert state["fg"].hwnd == 102

    with pytest.raises(PCControlError, match="terminal|blocked"):
        ctrl.switch_app("Windows PowerShell")


# -- Item 2: Approvals & tool-list registration --------------------------------------------------------------------
def test_item2_pc_control_is_registered_in_tool_list_as_laptop_only():
    from prometheus.agents.base import REGISTRY
    from prometheus.orchestrator.registry import planner_line, runs_on, specs

    assert "pc_control" in REGISTRY
    assert runs_on("pc_control") == "laptop"
    spec_names = {s.name for s in specs(REGISTRY)}
    assert "pc_control" in spec_names
    assert "laptop only" in planner_line("pc_control", REGISTRY["pc_control"])


def test_item2_every_input_action_asks_first_and_reading_asks_once(make_ctx, monkeypatch):
    from prometheus.pc_control.agent import PCControlAgent
    from prometheus.pc_control.controller import PCController

    backend, state = _make_fake_backend()
    raw = Image.new("RGB", (640, 480), color=(0, 0, 0))
    uia_payload = json.dumps({"window": "Notes - Notepad", "focused": None, "controls": []})

    def factory(data_dir, killswitch=None):
        return PCController(
            data_dir,
            killswitch=killswitch,
            backend=backend,
            grabber=lambda: raw,
            ps_runner=lambda script, stop_check=None: uia_payload,
            password_checker=lambda stop_check=None: (False, ""),
        )

    monkeypatch.setattr("prometheus.pc_control.agent.make_controller", factory)
    ctx = make_ctx()
    store, approvals = ctx.store, ctx.approvals
    agent = PCControlAgent()

    # 1) Read asks once, and after approval + remember="exact", second read runs immediately without asking
    r1 = agent.run("read screen", {"action": "read_controls"}, ctx)
    assert r1.status == "waiting_approval"
    pending = store.approvals(status="pending")
    assert len(pending) == 1
    out1 = approvals.resolve(pending[0].id, True, remember="exact")
    assert "Notes - Notepad" in out1

    r2 = agent.run("take screenshot", {"action": "screenshot"}, ctx)
    assert r2.status == "done"
    assert store.approvals(status="pending") == []

    # 2) Every click, type, press_keys, switch_app MUST ask every time (never remembered, describes action)
    actions = [
        ({"action": "click", "x": 120, "y": 240, "button": "left"}, "120, 240", "click"),
        ({"action": "type", "text": "hello"}, "hello", "char"),
        ({"action": "press_keys", "keys": "ctrl+c"}, "ctrl+c", "keys"),
        ({"action": "switch_app", "app": "Calculator"}, "Calculator", "switch"),
    ]
    for inputs, desc_snippet, ev_kind in actions:
        before_events = len(state["events"])
        res = agent.run("act", inputs, ctx)
        assert res.status == "waiting_approval"
        assert desc_snippet.lower() in res.summary.lower()
        assert len(state["events"]) == before_events  # nothing executed before approval!
        pending = store.approvals(status="pending")
        assert len(pending) == 1
        assert desc_snippet.lower() in pending[0].summary.lower()
        approvals.resolve(pending[0].id, True, remember="exact")
        assert len(state["events"]) > before_events
        assert any(e[0] == ev_kind for e in state["events"][before_events:])

    # Repeating the exact same click still asks again, and rejecting executes nothing
    again = agent.run("click again", {"action": "click", "x": 120, "y": 240}, ctx)
    assert again.status == "waiting_approval"
    before_reject = len(state["events"])
    approvals.resolve(again.data["approval_id"], False)
    assert len(state["events"]) == before_reject


# -- Item 3: Never type into password fields, and never send secret-looking text -----------------------------------
def test_item3_refuses_password_field_via_uia_and_never_sends_keystrokes(tmp_path):
    from prometheus.pc_control.controller import PCController, PasswordFieldError

    backend, state = _make_fake_backend()
    # Real UIA focused_is_password check driven by PS JSON response (IsPassword=True OR name="Password")
    for payload in (
        {"name": "Input", "control_type": "Edit", "automation_id": "", "is_password": True},
        {"name": "Password", "control_type": "Edit", "automation_id": "", "is_password": False},
    ):
        ctrl = PCController(
            data_dir=tmp_path,
            backend=backend,
            ps_runner=lambda script, stop_check=None, p=payload: json.dumps(p),
        )
        with pytest.raises(PasswordFieldError, match="password"):
            ctrl.type_text("my-pass")
    assert state["events"] == []


def test_item3_refuses_secret_text_in_controller_and_agent_before_approval(make_ctx, monkeypatch):
    from prometheus.pc_control.agent import PCControlAgent
    from prometheus.pc_control.controller import PCController, SecretTextError

    backend, state = _make_fake_backend()
    fake_api_key = "sk-ant-" + "A1b2C3d4E5f6G7h8I9j0K1L2"

    ctrl = PCController(
        data_dir=make_ctx().home,
        backend=backend,
        password_checker=lambda stop_check=None: (False, ""),
    )
    with pytest.raises(SecretTextError, match="secret"):
        ctrl.type_text(fake_api_key)
    assert state["events"] == []

    monkeypatch.setattr(
        "prometheus.pc_control.agent.make_controller",
        lambda d, killswitch=None: PCController(
            d, killswitch=killswitch, backend=backend, password_checker=lambda stop_check=None: (False, "")
        ),
    )
    ctx = make_ctx()
    agent = PCControlAgent()
    res = agent.run("type key", {"action": "type", "text": fake_api_key}, ctx)
    assert res.status == "failed"
    assert fake_api_key not in res.summary
    assert ctx.store.approvals(status="pending") == []  # never even stored in approvals DB!


# -- Item 4: Stop all stops at once; a running action ends within a second -----------------------------------------
def test_item4_killswitch_stops_running_type_action_under_one_second(tmp_path):
    import threading
    import time
    from prometheus.pc_control.controller import PCController, StoppedError
    from prometheus.safety.killswitch import KillSwitch

    ks = KillSwitch()
    started = threading.Event()
    state = {"chars": []}

    class SlowFakeWin32:
        def foreground_window(self):
            return None

        def type_text(self, text: str, stop_check=None):
            for ch in text:
                if stop_check and stop_check():
                    raise StoppedError("stopped")
                state["chars"].append(ch)
                started.set()
                time.sleep(0.15)

    ctrl = PCController(
        data_dir=tmp_path,
        killswitch=ks,
        backend=SlowFakeWin32(),
        password_checker=lambda stop_check=None: (False, ""),
    )
    errors = []
    done = threading.Event()

    def _run():
        try:
            ctrl.type_text("a" * 30)  # would take ~4.5s unstopped
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    assert started.wait(timeout=2.0)

    t0 = time.monotonic()
    ks.trigger("owner pressed stop all")
    assert done.wait(timeout=1.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    assert ctrl._abort_flag.is_set()
    assert errors and isinstance(errors[0], StoppedError)
    assert len(state["chars"]) < 10


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell subprocess test")
def test_item4_killswitch_kills_active_powershell_uia_under_one_second(tmp_path):
    import threading
    import time
    from prometheus.pc_control.controller import PCController, StoppedError
    from prometheus.pc_control.uia import run_ps_abortable
    from prometheus.safety.killswitch import KillSwitch

    ks = KillSwitch()
    ctrl = PCController(data_dir=tmp_path, killswitch=ks)
    errors = []
    done = threading.Event()

    def _run():
        try:
            run_ps_abortable("Write-Output 'ready'; Start-Sleep -Seconds 10", stop_check=ctrl.is_stopped, timeout=12.0)
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.35)
    t0 = time.monotonic()
    ks.trigger("stop all")
    assert done.wait(timeout=1.0)
    elapsed = time.monotonic() - t0
    assert elapsed < 1.0
    assert ctrl._abort_flag.is_set()
    assert errors and isinstance(errors[0], StoppedError)


def test_item4_agent_refuses_immediately_when_stopped(make_ctx):
    from prometheus.pc_control.agent import PCControlAgent

    ctx = make_ctx()
    ctx.killswitch.trigger("stop")
    res = PCControlAgent().run("click", {"action": "click", "x": 10, "y": 10}, ctx)
    assert res.status == "failed"
    assert "stop" in res.summary.lower()
    assert ctx.store.approvals(status="pending") == []

