"""Keeps Prometheus up to date by itself: checks GitHub every few hours and installs new versions.

It only fast-forwards (never overwrites changes made on this laptop), waits while a task is running, and the
new version is used from the next start. In background mode (started with Windows) it restarts itself when idle.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from prometheus.launcher import CODE_DIR, update

CHECK_EVERY = 6 * 3600       # seconds between checks
FIRST_CHECK = 90             # seconds after start, so starting up stays fast
CODE_CHECK = 60              # seconds between looks at whether the code was updated from outside


class AutoUpdater:
    def __init__(self, services, code_dir: Path = CODE_DIR, run=None, updater=update):
        self.services = services
        self.code_dir = Path(code_dir)
        self.run = run or subprocess.run
        self.updater = updater
        self.updated = ""        # shown in the app once a new version is installed
        self.last_error = ""
        self._stop = threading.Event()
        self.started_at = ""                 # the code this process is running (read when the loop starts)

    def head(self) -> str:
        """The commit the code folder is on now ('' when it isn't a Git checkout)."""
        if not (self.code_dir / ".git").exists():
            return ""
        try:
            return self._git("rev-parse", "HEAD")
        except Exception:
            return ""

    def code_changed(self) -> bool:
        """True when the code on disk is newer than what's running (updated by the installer, by hand…).
        Running on: new pages would call buttons this old process doesn't know ("Not Found")."""
        now = self.head()
        return bool(self.started_at and now and now != self.started_at)

    def _git(self, *args: str) -> str:
        proc = self.run(["git", "-C", str(self.code_dir), *args], capture_output=True, text=True, encoding="utf-8",
                        errors="replace", timeout=120)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip()[-200:] or "git failed")
        return (proc.stdout or "").strip()

    def behind(self) -> int:
        """How many new versions (commits) are waiting on GitHub."""
        self._git("fetch", "--quiet", "origin", "main")
        return int(self._git("rev-list", "--count", "HEAD..origin/main") or 0)

    def check_once(self) -> str:
        if not self.services.settings.auto_update or not (self.code_dir / ".git").exists():
            return ""
        if self.services.running:            # never update in the middle of a task
            return ""
        try:
            if self.behind() <= 0:
                return ""
            self.updater(self.code_dir)
        except Exception as e:                # offline, signed out of GitHub, local changes…
            self.last_error = str(e)[:200]
            return ""
        from prometheus.launcher import version

        try:
            self.services.store.kv_set("update_to", version(self.code_dir))
        except AttributeError:
            pass
        self.updated = "A new version was installed. Prometheus restarts by itself when nothing is running."
        self.services.store.audit("system", "auto_update", {})
        return self.updated

    def loop(self, background: bool = False) -> None:
        self.started_at = self.head()
        last_check = time.monotonic() - CHECK_EVERY + FIRST_CHECK
        while not self._stop.wait(CODE_CHECK):
            if self.code_changed():
                self.updated = "Prometheus was updated. It restarts by itself when nothing is running."
                self.restart_when_idle()
                return
            if time.monotonic() - last_check >= CHECK_EVERY:
                last_check = time.monotonic()
                if self.check_once():
                    self.restart_when_idle()
                    return

    def busy(self) -> str:
        """What would be interrupted by a restart right now ('' = nothing)."""
        s = self.services
        if s.running:
            return "a task"
        voice = getattr(s, "voice", None)
        if voice is not None and voice.state() in ("listening", "thinking", "speaking"):
            return "a voice command"
        from prometheus.voice import reader

        if reader.CURRENT is not None and reader.CURRENT.state in ("reading", "explaining"):
            return "reading aloud"
        try:
            quiz = s.store._rows("SELECT 1 FROM quiz WHERE started > ? LIMIT 1", (time.time() - 1800,))
        except Exception:
            quiz = []
        if quiz:
            return "a quiz"
        return ""

    def restart_when_idle(self, mode: str | None = None, spawn=None, leave=None) -> None:
        """Start the new version the same way this one was started (window, background or server) as soon as
        nothing is running, and let this one end. The new one says what's new."""
        while self.busy():
            if self._stop.wait(5):
                return
        mode = mode or os.environ.get("PROMETHEUS_MODE", "background")
        if getattr(self.services.settings, "auto_restart", True) is False:
            return
        voice = getattr(self.services, "voice", None)
        if voice is not None:
            voice.stop()
        self.services.store.kv_set("just_updated", self.services.store.kv_get("update_to", "") or "1")
        exe = Path(sys.executable)
        quiet = exe.with_name("pythonw.exe")
        args = {"app": ["app"], "serve": ["serve"]}.get(mode, ["background", "--after-update"])
        (spawn or subprocess.Popen)([str(quiet if quiet.exists() else exe), "-m", "prometheus.cli", *args],
                                    cwd=str(self.code_dir), close_fds=True,
                                    creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        (leave or os._exit)(0)

    def start(self, background: bool = False) -> None:
        threading.Thread(target=self.loop, args=(background,), name="prometheus-update", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
