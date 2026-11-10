"""Password hashing, sessions and login rate limiting."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time

SESSION_SECONDS = 12 * 3600
MAX_FAILURES = 5
FAILURE_WINDOW = 300


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_hex, digest_hex = stored.split("$")
    except ValueError:
        return False
    if scheme != "scrypt":
        return False
    digest = hashlib.scrypt(password.encode("utf-8"), salt=bytes.fromhex(salt_hex), n=2**14, r=8, p=1)
    return hmac.compare_digest(digest.hex(), digest_hex)


class Sessions:
    def __init__(self):
        self._lock = threading.Lock()
        self._items: dict[str, tuple[str, float]] = {}

    def create(self) -> tuple[str, str]:
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self._lock:
            self._items[sid] = (csrf, time.time() + SESSION_SECONDS)
        return sid, csrf

    def get(self, sid: str | None) -> str | None:
        if not sid:
            return None
        with self._lock:
            item = self._items.get(sid)
            if not item:
                return None
            if item[1] < time.time():
                del self._items[sid]
                return None
            return item[0]

    def drop(self, sid: str | None) -> None:
        with self._lock:
            self._items.pop(sid or "", None)


class LoginLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}

    def attempt(self, client: str) -> bool:
        """Count a login attempt *before* the slow password check. False = too many tries."""
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(client, []) if now - t < FAILURE_WINDOW]
            if len(recent) >= MAX_FAILURES:
                self._failures[client] = recent
                return False
            recent.append(now)
            self._failures[client] = recent
            return True

    def reset(self, client: str) -> None:
        with self._lock:
            self._failures.pop(client, None)
