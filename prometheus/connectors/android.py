"""Minimal ADB pairing/reachability compatibility helpers (M6 6.5).

All interactive phone control now runs through @mobilenext/mobile-mcp in Other apps (prometheus.phone.MobilePhone).
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess

from prometheus.phone import MobilePhone, PhoneError

TARGET = re.compile(r"^[A-Za-z0-9.:\[\]-]{3,80}$")


def _run(args: list[str], timeout: int = 30) -> tuple[int, bytes]:
    proc = subprocess.run(args, capture_output=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


class Phone:
    """Wireless debugging attach + compatibility facade kept for phone_wait, device status, and voice briefing."""

    def __init__(self, target: str = "", adb: str = "", run=_run):
        self.target = target
        self.adb = adb or shutil.which("adb") or "adb"
        self.run = run

    def _cmd(self, *args: str, timeout: int = 30) -> str:
        if not self.target:
            raise PhoneError("Your phone isn't connected right now: Settings → Phone control.")
        try:
            code, out = self.run([self.adb, "-s", self.target, *args], timeout)
        except FileNotFoundError as e:
            raise PhoneError("adb isn't installed on this laptop.") from e
        except subprocess.TimeoutExpired as e:
            raise PhoneError("The phone didn't answer in time.") from e
        text = out.decode("utf-8", errors="replace")
        if code != 0 or "device offline" in text or ("device '" in text and "not found" in text):
            raise PhoneError("The phone isn't connected right now. Open Settings → Phone control → Connect.")
        return text

    def shell(self, *parts: str, timeout: int = 30) -> str:
        """Minimal fixed check kept for phone_wait reachability ('echo ok', 'getprop')."""
        allowed = {"echo", "getprop"}
        if not parts or parts[0] not in allowed:
            raise PhoneError("Direct phone shell control was replaced by mobile-mcp.")
        return self._cmd("shell", " ".join(shlex.quote(p) for p in parts), timeout=timeout)

    def pair(self, target: str, code: str) -> str:
        if not TARGET.fullmatch(target or "") or not re.fullmatch(r"\d{6}", code or ""):
            raise PhoneError("Pairing needs the address shown on the phone (like 100.64.1.2:37123) and its 6 digits.")
        _, out = self.run([self.adb, "pair", target, code], 30)
        text = out.decode("utf-8", errors="replace")
        if "Successfully paired" not in text:
            raise PhoneError("Pairing didn't work: check the code and address (they change each time).")
        return "Paired."

    def connect(self, target: str) -> str:
        if not TARGET.fullmatch(target or ""):
            raise PhoneError("The address looks like 100.64.1.2:41234 (IP and port from Wireless debugging).")
        _, out = self.run([self.adb, "connect", target], 30)
        text = out.decode("utf-8", errors="replace")
        if "connected to" not in text:
            raise PhoneError("Couldn't connect: is wireless debugging on, and is the port the one shown now?")
        self.target = target
        return f"Connected to your phone ({self.model()})."

    def model(self) -> str:
        try:
            return self.shell("getprop", "ro.product.model").strip() or "Android"
        except PhoneError:
            return "Android"

    def notifications(self) -> list[dict]:
        return MobilePhone(self.target).notifications()

    def battery(self) -> str:
        return MobilePhone(self.target).battery()
