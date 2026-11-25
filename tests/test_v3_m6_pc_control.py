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
