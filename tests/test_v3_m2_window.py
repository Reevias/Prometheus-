"""M2.1 desktop window: remembers its monitor and place; Ctrl+Alt+P brings Prometheus to the front (Windows)."""
from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import pytest

from prometheus.desktop import (let_taskbar_minimise, minimisable_style, on_a_screen, remember_position,
                                start_position, work_area_bounds)
from prometheus.hotkey import GlobalHotkey

LAPTOP = SimpleNamespace(x=0, y=0, width=1920, height=1080)
LEFT_MONITOR = SimpleNamespace(x=-2560, y=0, width=2560, height=1440)


def test_position_is_kept_only_if_that_screen_is_there():
    assert on_a_screen(100, 100, [LAPTOP]) and on_a_screen(-1000, 300, [LAPTOP, LEFT_MONITOR])
    assert not on_a_screen(-1000, 300, [LAPTOP])                    # the left monitor was unplugged
    s = SimpleNamespace(window_x=-2000, window_y=200)
    assert start_position(s, SimpleNamespace(screens=[LAPTOP, LEFT_MONITOR])) == {"x": -2000, "y": 200}
    assert start_position(s, SimpleNamespace(screens=[LAPTOP])) == {}       # Windows places it instead
    assert start_position(SimpleNamespace(window_x=None, window_y=None), SimpleNamespace(screens=[LAPTOP])) == {}


class Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, fn):
        self.handlers.append(fn)
        return self

    def fire(self, *a):
        for h in self.handlers:
            h(*a)


def test_moving_saves_once_it_stops():
    window = SimpleNamespace(events=SimpleNamespace(moved=Event()))
    settings = SimpleNamespace(window_x=None, window_y=None)
    saved = []
    remember_position(window, settings, save=lambda s: saved.append((s.window_x, s.window_y)), delay=0.1)
    for x in range(0, 500, 50):
        window.events.moved.fire(x, 40)                              # dragging: many moves, one save
    time.sleep(0.3)
    assert saved == [(450, 40)] and (settings.window_x, settings.window_y) == (450, 40)


@pytest.mark.skipif(sys.platform == "win32", reason="checks the non-Windows answer")
def test_hotkey_is_windows_only():
    hk = GlobalHotkey()
    assert hk.start(lambda: None) is False and "Windows" in hk.error


@pytest.mark.skipif(sys.platform != "win32", reason="needs native Windows")
def test_hotkey_registers_and_stops_on_windows():
    hk = GlobalHotkey()
    ok = hk.start(lambda: None)
    assert ok or "already used" in hk.error                         # another app may own Ctrl+Alt+P on the runner
    hk.stop()
    assert not hk.registered


def test_maximised_window_stops_at_the_taskbar():
    # main screen 1920x1080 with a 48 px taskbar at the bottom: maximised fills 1920x1032 from its corner
    assert work_area_bounds((0, 0), (0, 0, 1920, 1032)) == (0, 0, 1920, 1032)
    # a left monitor at x=-2560 with the taskbar on its left: measured from that monitor's own corner
    assert work_area_bounds((-2560, 0), (-2500, 0, 2500, 1440)) == (60, 0, 2500, 1440)


def test_taskbar_click_can_minimise_the_frameless_window():
    frameless = 0x16000000                      # WS_POPUP-style window with no caption, as WinForms makes it
    style = minimisable_style(frameless)
    assert style & 0x00020000 and style & 0x00080000 and style & 0x00010000        # minimise, system menu, maximise
    assert not style & 0x00C00000                                                 # still no title bar (WS_CAPTION)
    assert minimisable_style(style) == style and minimisable_style(0x96000000) <= 0xFFFFFFFF


@pytest.mark.skipif(sys.platform != "win32", reason="needs native Windows")
def test_taskbar_minimise_style_on_a_real_windows_window():
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, wintypes.LPVOID]
    hwnd = user32.CreateWindowExW(0, "STATIC", "test", 0x80000000, 0, 0, 100, 100, None, None, None, None)   # WS_POPUP
    assert hwnd
    try:
        assert let_taskbar_minimise(hwnd)
        user32.GetWindowLongW.restype = ctypes.c_long
        style = user32.GetWindowLongW(wintypes.HWND(hwnd), -16) & 0xFFFFFFFF
        assert style & 0x00020000 and not style & 0x00C00000
    finally:
        user32.DestroyWindow(wintypes.HWND(hwnd))


def test_not_windows_does_nothing():
    if sys.platform != "win32":
        assert let_taskbar_minimise(1234) is False
