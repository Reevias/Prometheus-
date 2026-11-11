"""Search inside files: reads the text of documents in the main folders (Documents, Desktop, Downloads, OneDrive and
folders added in Settings) so "the file that mentions invoice 4521" can be found, not only by name.

Reading is slow on purpose: at night (23:45 to 07:00) a little at a time, or when the owner presses "Read my files
now". Only changed files are read again; deleted files are forgotten. Everything stays in memory.db on the laptop and
is private (never sent to a cloud model).
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path

from prometheus.agents.textutil import extract_text

SUFFIXES = {".pdf", ".docx", ".xlsx", ".pptx", ".txt", ".md", ".csv"}
MAX_BYTES = 40 * 1024 * 1024
READ_CHARS = 200_000
MAIN = ("Documents", "Desktop", "Downloads", "OneDrive")
SKIP = {"node_modules", ".git", "__pycache__", ".venv", "venv", "site-packages", "appdata", "$recycle.bin",
        "cache", ".cache", "temp", "tmp"}
NIGHT = (23 * 60 + 45, 7 * 60)          # minutes after midnight: start, end
CHECK_EVERY = 600
PAUSE = 0.2                              # seconds between files, so the laptop stays quick
CURRENT = None                           # the app's FileIndex
SCHEMA = ("CREATE TABLE IF NOT EXISTS {table} (path TEXT PRIMARY KEY, mtime REAL NOT NULL, size INTEGER NOT NULL, "
          "doc_id INTEGER, read REAL NOT NULL)")


def is_night(now: float) -> bool:
    lt = time.localtime(now)
    m = lt.tm_hour * 60 + lt.tm_min
    return m >= NIGHT[0] or m < NIGHT[1]


class FileIndex:
    TABLE, SUFFIXES, MAX_BYTES, MAIN, KV = "file_index", SUFFIXES, MAX_BYTES, MAIN, "file_index_status"
    FEATURE = "file_search"          # Settings → Features switch for the nightly reading

    def __init__(self, store, policy, extra_roots=(), clock=time.time, pause: float = PAUSE):
        self.store, self.policy, self.clock, self.pause = store, policy, clock, pause
        self.extra_roots = [Path(r) for r in extra_roots]
        self._stop = threading.Event()
        self._busy = threading.Lock()
        self.status = ""
        with store._lock:
            store._db.execute(SCHEMA.format(table=self.TABLE))
            store._db.commit()

    # -- where to read -----------------------------------------------------------------
    def places(self) -> list[Path]:
        home = self.policy.home
        out = [home / name for name in self.MAIN if (home / name).is_dir()]
        out += [p for p in home.glob("OneDrive - *") if p.is_dir()]
        out += [r for r in self.extra_roots if r.is_dir()]
        return out

    def files(self):
        for start in self.places():
            stack = [start]
            while stack:
                folder = stack.pop()
                try:
                    entries = list(os.scandir(folder))
                except OSError:
                    continue
                for e in entries:
                    try:
                        if e.is_symlink() or e.name.startswith((".", "~$")):
                            continue
                        p = Path(e.path)
                        if e.is_dir(follow_symlinks=False):
                            if e.name.lower() not in SKIP and not self.policy.is_blocked(p):
                                stack.append(p)
                        elif p.suffix.lower() in self.SUFFIXES and not self.policy.is_blocked(p):
                            st = e.stat()
                            if 0 < st.st_size <= self.MAX_BYTES:
                                yield p, st
                    except OSError:
                        continue

    # -- reading -------------------------------------------------------------------------
    def known(self) -> dict[str, tuple[float, int]]:
        return {r["path"]: (r["mtime"], r["size"])
                for r in self.store._rows(f"SELECT path, mtime, size FROM {self.TABLE}")}

    def read(self, path: Path) -> str:
        return extract_text(path, limit=READ_CHARS)

    def run(self, budget_seconds: float = 3600, only_at_night: bool = False) -> dict:
        """Read new and changed files until the time budget is used. Returns counts."""
        if not self._busy.acquire(blocking=False):
            return {"read": 0, "forgotten": 0, "busy": True}
        try:
            return self._run(budget_seconds, only_at_night)
        finally:
            self._busy.release()

    def _run(self, budget_seconds: float, only_at_night: bool) -> dict:
        end = time.monotonic() + budget_seconds
        known, seen, read, skipped = self.known(), set(), 0, 0
        for path, st in self.files():
            key = str(path)
            seen.add(key)
            if known.get(key) == (st.st_mtime, st.st_size):
                continue
            if self._stop.is_set() or time.monotonic() > end or (only_at_night and not is_night(self.clock())):
                skipped += 1
                continue
            try:
                text = self.read(path)
            except ImportError:                   # a missing package: stop, and say how to get it
                raise
            except Exception:
                text = ""
            doc_id = None
            if text.strip():
                doc_id = self.store.add_document(f"file:{path}", path.name, key, text, private=True)
            with self.store._lock:
                self.store._db.execute(f"INSERT OR REPLACE INTO {self.TABLE} (path, mtime, size, doc_id, read) "
                                       "VALUES (?, ?, ?, ?, ?)", (key, st.st_mtime, st.st_size, doc_id, self.clock()))
                self.store._db.commit()
            read += 1
            self.status = f"Read {read} files…"
            if self.pause:
                time.sleep(self.pause)
        forgotten = 0
        if not skipped:                        # a full pass: forget files that are gone
            for key in set(known) - seen:
                self.forget(key)
                forgotten += 1
        total = self.store._rows(f"SELECT COUNT(*) AS n FROM {self.TABLE}")[0]["n"]
        self.status = f"{total} files read" + (f", {skipped} still to read" if skipped else "")
        self.store.kv_set(self.KV, self.status)
        self.after_run(read)
        return {"read": read, "forgotten": forgotten, "left": skipped, "total": total}

    def after_run(self, read: int) -> None:
        pass

    def forget(self, key: str) -> None:
        rows = self.store._rows(f"SELECT doc_id FROM {self.TABLE} WHERE path = ?", (key,))
        if rows and rows[0]["doc_id"]:
            self.store.delete_document(int(rows[0]["doc_id"]))
        with self.store._lock:
            self.store._db.execute(f"DELETE FROM {self.TABLE} WHERE path = ?", (key,))
            self.store._db.commit()

    # -- searching -----------------------------------------------------------------------
    def search(self, query: str, limit: int = 8) -> list[dict]:
        """Files whose text matches, best first, each with the passage that matched."""
        hits = self.store.search_chunks(query, limit=limit * 3, include_private=True, chats=False)
        out, seen = [], set()
        for h in hits:
            if not str(h.source).startswith(tuple(str(p) for p in self.policy.roots())) or h.source in seen:
                continue
            if not Path(h.source).exists():
                continue
            seen.add(h.source)
            out.append({"path": h.source, "name": h.title, "passage": " ".join(h.match.split())[:300]})
            if len(out) >= limit:
                break
        return out

    # -- the night clock ---------------------------------------------------------------------
    def tick(self) -> dict | None:
        if is_night(self.clock()):
            return self.run(budget_seconds=CHECK_EVERY - 30, only_at_night=True)
        return None

    def loop(self) -> None:
        from prometheus.features import running

        while not self._stop.wait(CHECK_EVERY):
            if not running(None, self.FEATURE):
                continue
            try:
                self.tick()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-file-index", daemon=True).start()

    def read_now(self) -> None:
        threading.Thread(target=self.run, kwargs={"budget_seconds": 4 * 3600}, name="prometheus-file-index-now",
                         daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
