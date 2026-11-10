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


class AutoUpdater:
    def __init__(self, services, code_dir: Path = CODE_DIR, run=None, updater=update):
        self.services = services
        self.code_dir = Path(code_dir)
        self.run = run or subprocess.run
        self.updater = updater
        self.updated = ""        # shown in the app once a new version is installed
        self.last_error = ""
        self._stop = threading.Event()

    def _git(self, *args: str) -> str:
        proc = self.run(["git", "-C", str(self.code_dir), *args], capture_output=True, text=True, timeout=120)
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
        self.updated = "A new version was installed. Restart Prometheus to use it."
        self.services.store.audit("system", "auto_update", {})
        return self.updated

    def loop(self, background: bool = False) -> None:
        if self._stop.wait(FIRST_CHECK):
            return
        while True:
            if self.check_once() and background:
                self.restart_when_idle()
            if self._stop.wait(CHECK_EVERY):
                return

    def restart_when_idle(self) -> None:
        """Background mode only: start the new version and let this one end."""
        voice = self.services.voice
        while self.services.running or (voice is not None and voice.state() in ("listening", "thinking", "speaking")):
            if self._stop.wait(5):
                return
        if voice is not None:
            voice.stop()
        exe = Path(sys.executable)
        quiet = exe.with_name("pythonw.exe")
        subprocess.Popen([str(quiet if quiet.exists() else exe), "-m", "prometheus.cli", "background", "--after-update"],
                         cwd=str(self.code_dir), close_fds=True,
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        os._exit(0)

    def start(self, background: bool = False) -> None:
        threading.Thread(target=self.loop, args=(background,), name="prometheus-update", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
