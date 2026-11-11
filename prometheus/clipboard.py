"""Clipboard and screenshot memory: "what was that link I copied this morning?"

On Windows, every 2 seconds the text on the clipboard is checked; new text and links are kept for 7 days. New
screenshots (Pictures\\Screenshots, OneDrive's too) have their text read on the laptop and are kept the same way.
Passwords, card numbers, OTPs, keys and anything copied from a password box are never kept. Everything stays in
memory.db; nothing is sent anywhere.
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

SCHEMA = ("CREATE TABLE IF NOT EXISTS clips (id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, "
          "text TEXT NOT NULL, path TEXT NOT NULL DEFAULT '')")
KEEP_DAYS = 7
POLL = 2.0
MAX_CHARS = 4000
LINK = re.compile(r"https?://\S+", re.I)
SECRET = re.compile(
    r"(?i)pass(word|phrase|wd)\s*[:=]|\bpin\b\s*[:=]?\s*\d|\botp\b|\bcvv\b|"
    r"sk-[A-Za-z0-9_-]{8,}|ghp_[A-Za-z0-9]{10,}|github_pat_|xox[bp]-|AKIA[0-9A-Z]{12,}|AIza[0-9A-Za-z_-]{20,}|"
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|\b(?:\d[ -]?){13,19}\b|^\s*\d{4,8}\s*$|"
    r"^[A-Za-z0-9+/_=-]{24,}$")
CURRENT = None


def is_secret(text: str) -> bool:
    t = (text or "").strip()
    if SECRET.search(t):
        return True
    # a single "word" with letters, digits and symbols mixed, like a password
    if re.fullmatch(r"\S{8,64}", t) and re.search(r"[A-Za-z]", t) and re.search(r"\d", t) \
            and re.search(r"[^A-Za-z0-9]", t) and not LINK.match(t):
        return True
    return False


def windows_clipboard_text() -> str:
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    # apps mark password-like copies with these formats: never read them
    for name in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory"):
        fmt = user32.RegisterClipboardFormatW(name)
        if fmt and user32.IsClipboardFormatAvailable(fmt) and name.startswith("Exclude"):
            return ""
    if not user32.IsClipboardFormatAvailable(13):            # CF_UNICODETEXT
        return ""
    if not user32.OpenClipboard(None):
        return ""
    try:
        handle = user32.GetClipboardData(13)
        if not handle:
            return ""
        ptr = kernel32.GlobalLock(handle)
        try:
            return ctypes.wstring_at(ptr)[:MAX_CHARS * 2] if ptr else ""
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


class ClipMemory:
    def __init__(self, store, home: Path, settings=None, reader=None, ocr=None, clock=time.time):
        self.store, self.home, self.settings, self.clock = store, Path(home), settings, clock
        self.reader = reader                   # () -> current clipboard text; None off Windows
        self.ocr = ocr                         # (path) -> text in the image
        self._last = None
        self._seen: set[str] = set()
        self._stop = threading.Event()
        with store._lock:
            store._db.execute(SCHEMA)
            store._db.commit()

    def on(self) -> bool:
        return bool(getattr(self.settings, "clip_on", True))

    # -- keeping -------------------------------------------------------------------------
    def keep(self, text: str, kind: str = "", path: str = "") -> int | None:
        text = (text or "").strip()[:MAX_CHARS]
        if not text or is_secret(text):
            return None
        kind = kind or ("link" if LINK.fullmatch(text) or (LINK.search(text) and len(text) < 300) else "text")
        last = self.store._rows("SELECT text FROM clips WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,))
        if last and last[0]["text"] == text:
            return None
        return self.store._write("INSERT INTO clips (created, kind, text, path) VALUES (?, ?, ?, ?)",
                                 (self.clock(), kind, text, path))

    def check_clipboard(self) -> int | None:
        if self.reader is None or not self.on():
            return None
        try:
            text = self.reader()
        except Exception:
            return None
        if text == self._last:
            return None
        self._last = text
        return self.keep(text)

    def screenshot_folders(self) -> list[Path]:
        out = [self.home / "Pictures" / "Screenshots"]
        out += [p / "Pictures" / "Screenshots" for p in self.home.glob("OneDrive*")]
        return [p for p in out if p.is_dir()]

    def check_screenshots(self) -> int:
        if self.ocr is None or not self.on():
            return 0
        since = float(self.store.kv_get("clips_shots_since", 0) or 0) or self.clock() - 3600
        added = 0
        for folder in self.screenshot_folders():
            for p in folder.iterdir():
                try:
                    if p.suffix.lower() not in (".png", ".jpg", ".jpeg") or str(p) in self._seen:
                        continue
                    if p.stat().st_mtime <= since:
                        continue
                except OSError:
                    continue
                self._seen.add(str(p))
                try:
                    words = self.ocr(p)
                except Exception:
                    words = ""
                if len(words.strip()) >= 3 and self.keep(words, "screenshot", str(p)):
                    added += 1
        self.store.kv_set("clips_shots_since", self.clock())
        return added

    def prune(self) -> None:
        days = int(getattr(self.settings, "clip_days", KEEP_DAYS) or KEEP_DAYS)
        self.store._write("DELETE FROM clips WHERE created < ?", (self.clock() - days * 86400,))

    # -- finding -------------------------------------------------------------------------
    def find(self, query: str = "", kind: str = "", since: float = 0, limit: int = 10) -> list[dict]:
        where, args = ["created >= ?"], [since]
        if kind:
            where.append("kind = ?")
            args.append(kind)
        words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) > 2][:6]
        rows = [dict(r) for r in self.store._rows(f"SELECT * FROM clips WHERE {' AND '.join(where)} ORDER BY id "
                                                  "DESC LIMIT 500", tuple(args))]
        if words:
            scored = [(sum(w in r["text"].lower() for w in words), r) for r in rows]
            rows = [r for s, r in sorted(scored, key=lambda x: -x[0]) if s]
        return rows[:limit]

    # -- the background watcher ------------------------------------------------------------
    def loop(self) -> None:
        n = 0
        from prometheus.features import running

        while not self._stop.wait(POLL):
            n += 1
            if not running(None, "clipboard"):
                continue
            try:
                self.check_clipboard()
                if n % 15 == 0:                     # every 30 seconds
                    self.check_screenshots()
                if n % 1800 == 0:                   # every hour
                    self.prune()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-clipboard", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()


def when_from(text: str, now: float) -> float:
    """'this morning' / 'today' / 'yesterday' / 'last hour' → the start of that time (0 for 'any time')."""
    t = (text or "").lower()
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if "yesterday" in t:
        return midnight - 86400
    if "morning" in t or "today" in t or "aaj" in t:
        return midnight
    if "hour" in t:
        return now - 3600
    if "minute" in t:
        return now - 1800
    return 0
