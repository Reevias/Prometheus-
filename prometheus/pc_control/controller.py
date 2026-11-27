"""High-level safe PC controller combining screenshot, UIA, mouse, keyboard, and app switching (M6 6.4a)."""
from __future__ import annotations

from pathlib import Path
import re
import threading
from typing import Any, Callable
import weakref

from prometheus.pc_control.errors import PCControlError, PasswordFieldError, SecretTextError, StoppedError
from prometheus.pc_control.screen import ScreenshotResult, capture_screenshot
from prometheus.pc_control.uia import abort_active_ps, focused_is_password, read_controls
from prometheus.pc_control.win32 import (
    MAX_TYPE_CHARS,
    Win32Backend,
    WindowInfo,
    is_blocked_window,
    normalize_combo,
)
from prometheus.safety.secrets import secret_problem

_EXTRA_SECRET_RE = re.compile(
    r"(?:\bBearer\s+[A-Za-z0-9._\-]{20,}|\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_\-]+)?)",
    re.IGNORECASE,
)


def check_safe_text(text: str) -> None:
    """Validate text length/characters and refuse anything that looks like an API key, token, or saved secret."""
    if not isinstance(text, str) or not text:
        raise PCControlError("Text to type cannot be empty.")
    if len(text) > MAX_TYPE_CHARS:
        raise PCControlError(f"Text is too long ({len(text)} > {MAX_TYPE_CHARS} chars).")
    if any(ord(ch) < 32 for ch in text):
        raise PCControlError("Text contains control characters; use press_keys for Enter/Tab.")
    problem = secret_problem(text)
    if problem:
        raise SecretTextError(problem)
    if _EXTRA_SECRET_RE.search(text):
        raise SecretTextError("Blocked: this contains what looks like a secret token. Remove it and try again.")


def _writes_text(canon: str) -> bool:
    """True when a key combination puts text into the focused box (a character, space, or a paste)."""
    parts = canon.split("+")
    mods = {p for p in parts if p in ("ctrl", "alt", "shift", "win")}
    main = [p for p in parts if p not in mods]
    if mods & {"ctrl"} and main == ["v"] or mods == {"shift"} and main == ["insert"]:
        return True
    return not (mods - {"shift"}) and len(main) == 1 and (len(main[0]) == 1 or main[0] == "space")


class PCController:
    """Controls desktop inspection and input with strict foreground and stop checks."""

    def __init__(
        self,
        data_dir: Path,
        *,
        killswitch=None,
        backend=None,
        grabber: Callable[[], Any] | None = None,
        ps_runner: Callable[..., str] | None = None,
        password_checker: Callable[..., tuple[bool, str]] | None = None,
    ):
        self.data_dir = Path(data_dir)
        self.killswitch = killswitch
        self.backend = backend or Win32Backend()
        self._grabber = grabber
        self._ps_runner = ps_runner
        self._password_checker = password_checker
        self._abort_flag = threading.Event()
        if killswitch is not None and hasattr(killswitch, "on_stop"):
            ref = weakref.ref(self)

            def _on_stop(_reason: str = "") -> None:
                obj = ref()
                if obj is not None:
                    obj.abort()
                else:
                    abort_active_ps()

            killswitch.on_stop(_on_stop)

    def abort(self, _reason: str = "") -> None:
        self._abort_flag.set()
        abort_active_ps()

    def is_stopped(self) -> bool:
        if self._abort_flag.is_set():
            return True
        if self.killswitch is not None and getattr(self.killswitch, "is_set", False):
            return True
        return False

    def _check_stop(self) -> None:
        if self.is_stopped():
            raise StoppedError("Stopped by Stop all.")

    def _check_foreground_allowed(self) -> WindowInfo | None:
        fg = self.backend.foreground_window()
        if fg and is_blocked_window(fg.title, fg.process):
            raise PCControlError(f"Blocked terminal or admin window: {fg.title or fg.process}")
        return fg

    def screenshot(self) -> ScreenshotResult:
        self._check_stop()
        return capture_screenshot(self.data_dir, grabber=self._grabber)

    def read_controls(self, window_title: str = "", limit: int = 80) -> dict[str, Any]:
        self._check_stop()
        return read_controls(
            window_title=window_title,
            limit=limit,
            stop_check=self.is_stopped,
            ps_runner=self._ps_runner,
        )

    def list_windows(self) -> list[WindowInfo]:
        self._check_stop()
        return [w for w in self.backend.list_windows() if w.title]

    def switch_app(self, query: str) -> WindowInfo:
        self._check_stop()
        needle = (query or "").strip().lower()
        if not needle:
            raise PCControlError("App or window name is required.")
        wins = self.backend.list_windows()
        matches = [w for w in wins if needle in w.title.lower() or needle in w.process.lower()]
        if not matches:
            raise PCControlError(f"No open window matching {query!r}.")
        target = matches[0]
        if is_blocked_window(target.title, target.process):
            raise PCControlError(f"Blocked terminal or admin window: {target.title or target.process}")
        if not self.backend.set_foreground(target.hwnd):
            raise PCControlError(f"Could not switch to window {target.title!r}.")
        return target

    def click(self, x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
        self._check_stop()
        if clicks not in (1, 2):
            raise PCControlError("Clicks must be 1 or 2.")
        left, top, right, bottom = self.backend.virtual_screen_rect()
        xi, yi = int(x), int(y)
        if not (left <= xi < right and top <= yi < bottom):
            raise PCControlError(f"Coordinates ({xi}, {yi}) are outside screen bounds {(left, top, right, bottom)}.")
        fg = self._check_foreground_allowed()
        self.backend.click(xi, yi, button=button, clicks=clicks, stop_check=self.is_stopped)
        return {"x": xi, "y": yi, "button": button, "clicks": clicks, "window": fg.title if fg else ""}

    def type_text(self, text: str) -> dict[str, Any]:
        self._check_stop()
        check_safe_text(text)
        fg = self._check_foreground_allowed()
        checker = self._password_checker or (
            lambda stop_check=None: focused_is_password(stop_check=stop_check, ps_runner=self._ps_runner)
        )
        is_pwd, reason = checker(stop_check=self.is_stopped)
        if is_pwd:
            raise PasswordFieldError(f"Refused to type into password field: {reason}")
        self.backend.type_text(text, stop_check=self.is_stopped)
        return {"typed": len(text), "window": fg.title if fg else ""}

    def press_keys(self, keys: str) -> dict[str, Any]:
        self._check_stop()
        canon, vks = normalize_combo(keys)
        fg = self._check_foreground_allowed()
        if _writes_text(canon):      # a letter, a digit, a space or a paste is typing: never into a password box
            checker = self._password_checker or (
                lambda stop_check=None: focused_is_password(stop_check=stop_check, ps_runner=self._ps_runner)
            )
            is_pwd, reason = checker(stop_check=self.is_stopped)
            if is_pwd:
                raise PasswordFieldError(f"Refused to press {canon} in a password field: {reason}")
        self.backend.press_keys(vks, stop_check=self.is_stopped)
        return {"keys": canon, "window": fg.title if fg else ""}
