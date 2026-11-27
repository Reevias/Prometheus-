"""Exceptions for safe PC control (M6 6.4a)."""
from __future__ import annotations


class PCControlError(RuntimeError):
    """Base error raised when a PC control action is invalid or blocked."""


class PasswordFieldError(PCControlError):
    """Raised when attempting to type into a password or credential field."""


class SecretTextError(PCControlError):
    """Raised when text looks like an API key, token, or saved secret."""


class StoppedError(PCControlError):
    """Raised when Stop all / killswitch aborts a PC control action."""
