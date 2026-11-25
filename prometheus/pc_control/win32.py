"""Windows ctypes user32 wrappers for mouse, keyboard, and window switching (M6 6.4a)."""
from __future__ import annotations

import ctypes
from dataclasses import dataclass
import sys
import time
from typing import Callable

from prometheus.laptop import NEVER_IN_NAME, NEVER_OPEN, TERMINALS
from prometheus.pc_control.errors import PCControlError, StoppedError

# Blocked global key combinations that open command/system dialogs directly
BLOCKED_COMBOS = frozenset({
    ("ctrl", "alt", "delete"),
    ("ctrl", "shift", "escape"),
    ("win", "r"),
    ("win", "x"),
})

VK_CODES: dict[str, int] = {
    "ctrl": 0x11,
    "control": 0x11,
    "alt": 0x12,
    "menu": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "lwin": 0x5B,
    "enter": 0x0D,
    "return": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "del": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pgup": 0x21,
    "pagedown": 0x22,
    "pgdn": 0x22,
    "up": 0x26,
    "down": 0x28,
    "left": 0x25,
    "right": 0x27,
    "capslock": 0x14,
    "printscreen": 0x2C,
}
for _i in range(1, 13):
    VK_CODES[f"f{_i}"] = 0x6F + _i
for _c in "abcdefghijklmnopqrstuvwxyz":
    VK_CODES[_c] = ord(_c.upper())
for _d in "0123456789":
    VK_CODES[_d] = ord(_d)

MODIFIER_KEYS = frozenset({"ctrl", "control", "alt", "menu", "shift", "win", "lwin"})
EXTENDED_VKS = frozenset({0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B})
MAX_TYPE_CHARS = 500


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    title: str
    process: str = ""
    pid: int = 0
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)


def is_blocked_window(title: str, process: str = "") -> bool:
    """True when a window is a terminal, admin tool, or system dialog we must never touch."""
    tl = (title or "").strip().lower()
    pl = (process or "").strip().lower()
    pstem = pl[:-4] if pl.endswith(".exe") else pl
    if pl in TERMINALS or pstem in TERMINALS or pl in NEVER_OPEN or pstem in NEVER_OPEN:
        return True
    if tl in NEVER_OPEN:
        return True
    return any(term in tl for term in NEVER_IN_NAME)


def normalize_combo(keys: str) -> tuple[str, tuple[int, ...]]:
    """Validate and translate a key combo string such as 'ctrl+c' into (canonical, vks)."""
    raw = (keys or "").strip().lower()
    if not raw:
        raise PCControlError("Key combination cannot be empty.")
    parts = [p.strip() for p in raw.split("+") if p.strip()]
    if not parts or len(parts) > 4:
        raise PCControlError(f"Unsupported key combination: {keys!r}")
    seen: list[str] = []
    for p in parts:
        canon = {"control": "ctrl", "menu": "alt", "return": "enter", "esc": "escape",
                 "del": "delete", "pgup": "pageup", "pgdn": "pagedown", "lwin": "win"}.get(p, p)
        if canon not in VK_CODES:
            raise PCControlError(f"Unknown key {p!r} in {keys!r}")
        if canon not in seen:
            seen.append(canon)
    mods = tuple(sorted(k for k in seen if k in MODIFIER_KEYS))
    main = tuple(k for k in seen if k not in MODIFIER_KEYS)
    check_set = tuple(sorted(seen))
    for blocked in BLOCKED_COMBOS:
        if tuple(sorted(blocked)) == check_set:
            raise PCControlError(f"Blocked system shortcut: {keys!r}")
    ordered = mods + main
    if not ordered:
        raise PCControlError(f"No valid keys in {keys!r}")
    return "+".join(ordered), tuple(VK_CODES[k] for k in ordered)


if sys.platform == "win32":
    from ctypes import wintypes

    class _MOUSEINPUT(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", wintypes.WPARAM),
        ]

    class _KEYBDINPUT(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", wintypes.WPARAM),
        ]

    class _HARDWAREINPUT(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class _INPUT_UNION(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]

    class _INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", _INPUT_UNION)]

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]


def _proc_name_for_pid(pid: int) -> str:
    if not pid:
        return ""
    try:
        import psutil
        return psutil.Process(pid).name() or ""
    except Exception:
        return ""


class Win32Backend:
    """Native Windows user32 input & window management using ctypes."""

    _INPUT_MOUSE = 0
    _INPUT_KEYBOARD = 1
    _KEYEVENTF_EXTENDEDKEY = 0x0001
    _KEYEVENTF_KEYUP = 0x0002
    _KEYEVENTF_UNICODE = 0x0004
    _MOUSE_FLAGS = {
        "left": (0x0002, 0x0004),
        "right": (0x0008, 0x0010),
        "middle": (0x0020, 0x0040),
    }

    def __init__(self):
        if sys.platform != "win32":
            self._user32 = None
            return
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        try:
            self._dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)
        except Exception:
            self._dwmapi = None

    def _require_win32(self):
        if self._user32 is None:
            raise PCControlError("PC control input requires Windows.")
        return self._user32

    def virtual_screen_rect(self) -> tuple[int, int, int, int]:
        u32 = self._require_win32()
        left = int(u32.GetSystemMetrics(76))
        top = int(u32.GetSystemMetrics(77))
        width = int(u32.GetSystemMetrics(78)) or int(u32.GetSystemMetrics(0))
        height = int(u32.GetSystemMetrics(79)) or int(u32.GetSystemMetrics(1))
        return (left, top, left + width, top + height)

    def _window_info(self, hwnd: int) -> WindowInfo | None:
        u32 = self._require_win32()
        if not hwnd:
            return None
        length = int(u32.GetWindowTextLengthW(hwnd))
        title = ""
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            u32.GetWindowTextW(hwnd, buf, length + 1)
            title = buf.value.strip()
        pid = ctypes.wintypes.DWORD(0)
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        rect = _RECT()
        u32.GetWindowRect(hwnd, ctypes.byref(rect))
        return WindowInfo(
            hwnd=int(hwnd),
            title=title,
            process=_proc_name_for_pid(int(pid.value)),
            pid=int(pid.value),
            rect=(rect.left, rect.top, rect.right, rect.bottom),
        )

    def _is_cloaked(self, hwnd: int) -> bool:
        if not self._dwmapi:
            return False
        cloaked = ctypes.c_int(0)
        hr = self._dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        return hr == 0 and bool(cloaked.value)

    def foreground_window(self) -> WindowInfo | None:
        u32 = self._require_win32()
        hwnd = u32.GetForegroundWindow()
        return self._window_info(int(hwnd)) if hwnd else None

    def list_windows(self) -> list[WindowInfo]:
        u32 = self._require_win32()
        out: list[WindowInfo] = []
        enum_cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)

        def _visit(hwnd, _lparam):
            if not u32.IsWindowVisible(hwnd):
                return True
            if self._is_cloaked(hwnd):
                return True
            info = self._window_info(int(hwnd))
            if info and info.title:
                out.append(info)
            return True

        u32.EnumWindows(enum_cb(_visit), 0)
        return out

    def set_foreground(self, hwnd: int) -> bool:
        u32 = self._require_win32()
        if not u32.IsWindow(hwnd):
            return False
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)  # SW_RESTORE
        if u32.SetForegroundWindow(hwnd):
            return True
        fg = u32.GetForegroundWindow()
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        cur_tid = kernel32.GetCurrentThreadId()
        fg_tid = u32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        try:
            if fg_tid and fg_tid != cur_tid:
                attached = bool(u32.AttachThreadInput(cur_tid, fg_tid, True))
            u32.BringWindowToTop(hwnd)
            ok = bool(u32.SetForegroundWindow(hwnd))
            return ok or int(u32.GetForegroundWindow()) == int(hwnd)
        finally:
            if attached:
                u32.AttachThreadInput(cur_tid, fg_tid, False)

    def _send(self, inputs: list[_INPUT]) -> None:
        u32 = self._require_win32()
        arr = (_INPUT * len(inputs))(*inputs)
        sent = u32.SendInput(len(inputs), arr, ctypes.sizeof(_INPUT))
        if sent != len(inputs):
            err = ctypes.get_last_error()
            raise PCControlError(f"SendInput sent {sent}/{len(inputs)} events (winerr={err}).")

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1,
              stop_check: Callable[[], bool] | None = None) -> None:
        u32 = self._require_win32()
        flags = self._MOUSE_FLAGS.get(button)
        if not flags:
            raise PCControlError(f"Unsupported mouse button: {button!r}")
        if not u32.SetCursorPos(int(x), int(y)):
            raise PCControlError(f"Could not move cursor to ({x}, {y}).")
        down_flag, up_flag = flags
        for i in range(clicks):
            if stop_check and stop_check():
                raise StoppedError("Stopped before click.")
            down = _INPUT(type=self._INPUT_MOUSE, mi=_MOUSEINPUT(dwFlags=down_flag))
            up = _INPUT(type=self._INPUT_MOUSE, mi=_MOUSEINPUT(dwFlags=up_flag))
            self._send([down, up])
            if i + 1 < clicks:
                time.sleep(0.05)

    def type_text(self, text: str, stop_check: Callable[[], bool] | None = None) -> None:
        units = text.encode("utf-16-le")
        for idx in range(0, len(units), 2):
            if stop_check and stop_check():
                raise StoppedError("Stopped while typing.")
            code_unit = int.from_bytes(units[idx:idx + 2], "little")
            down = _INPUT(
                type=self._INPUT_KEYBOARD,
                ki=_KEYBDINPUT(wVk=0, wScan=code_unit, dwFlags=self._KEYEVENTF_UNICODE),
            )
            up = _INPUT(
                type=self._INPUT_KEYBOARD,
                ki=_KEYBDINPUT(
                    wVk=0,
                    wScan=code_unit,
                    dwFlags=self._KEYEVENTF_UNICODE | self._KEYEVENTF_KEYUP,
                ),
            )
            self._send([down, up])
            time.sleep(0.002)

    def press_keys(self, vks: tuple[int, ...], stop_check: Callable[[], bool] | None = None) -> None:
        if stop_check and stop_check():
            raise StoppedError("Stopped before key press.")
        events: list[_INPUT] = []
        for vk in vks:
            ext = self._KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
            events.append(_INPUT(type=self._INPUT_KEYBOARD, ki=_KEYBDINPUT(wVk=vk, dwFlags=ext)))
        for vk in reversed(vks):
            ext = self._KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VKS else 0
            events.append(
                _INPUT(type=self._INPUT_KEYBOARD, ki=_KEYBDINPUT(wVk=vk, dwFlags=ext | self._KEYEVENTF_KEYUP))
            )
        self._send(events)
