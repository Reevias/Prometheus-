"""Ctrl+Alt+P anywhere in Windows brings Prometheus to the front (M2).

Uses Windows' own RegisterHotKey (no extra package, no keyboard hook, nothing else is read). If another app already
owns the combination, Prometheus simply does without it and says so in `error`.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable

MOD_ALT, MOD_CONTROL, MOD_NOREPEAT = 0x0001, 0x0002, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
HOTKEY_ID = 0x5052          # "PR"
KEY_LABEL = "Ctrl+Alt+P"


class GlobalHotkey:
    def __init__(self, vk: int = ord("P"), modifiers: int = MOD_CONTROL | MOD_ALT):
        self.vk, self.modifiers = vk, modifiers
        self.error = ""
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self.registered = False

    def start(self, on_press: Callable[[], None]) -> bool:
        """Listen for the key in a background thread. False (with `error`) when it can't be used."""
        if sys.platform != "win32":
            self.error = "Only on Windows."
            return False
        self._thread = threading.Thread(target=self._run, args=(on_press,), name="prometheus-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return self.registered

    def _run(self, on_press: Callable[[], None]) -> None:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32")
        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID, self.modifiers | MOD_NOREPEAT, self.vk):
            self.error = f"{KEY_LABEL} is already used by another app, so it's off."
            self._ready.set()
            return
        self.registered = True
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    try:
                        on_press()
                    except Exception:
                        pass                    # a failed show never stops the hotkey
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
            self.registered = False

    def stop(self) -> None:
        if self._thread is None or not self._thread_id or sys.platform != "win32":
            return
        import ctypes

        ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
        self._thread.join(3)
