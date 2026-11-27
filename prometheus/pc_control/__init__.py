"""Control PC package: screenshots, UI Automation controls, mouse, keyboard, and app switching (M6 6.4a)."""
from __future__ import annotations

from prometheus.pc_control.agent import PCControlAgent, make_controller
from prometheus.pc_control.controller import PCController
from prometheus.pc_control.errors import (
    PCControlError,
    PasswordFieldError,
    SecretTextError,
    StoppedError,
)
from prometheus.pc_control.screen import ScreenshotResult
from prometheus.pc_control.uia import ControlInfo
from prometheus.pc_control.win32 import WindowInfo

__all__ = [
    "ControlInfo",
    "PCControlAgent",
    "PCControlError",
    "PCController",
    "PasswordFieldError",
    "ScreenshotResult",
    "SecretTextError",
    "StoppedError",
    "WindowInfo",
    "make_controller",
]
