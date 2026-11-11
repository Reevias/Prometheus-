"""Watchers: Prometheus speaks up by itself. Battery low, disk almost full, CPU busy for a while, new files in a
chosen folder (which can start a standing goal, e.g. "file new PDFs into Documents/PDFs").

Alerts go to the "Prometheus alerts" chat, a Windows notification, and the voice if it's on. Each alert is said once
until things change (battery charged again, disk freed, CPU calm).
"""
from __future__ import annotations

import os
import shutil
import threading
import time
from pathlib import Path

CHECK_EVERY = 60
BATTERY_LOW, BATTERY_OK = 20, 25
DISK_FULL = 0.90
CPU_BUSY, CPU_MINUTES = 90, 5
ALERTS_CHAT = "Prometheus alerts"
NEW_FILES_SHOWN = 20
EMAIL_EVERY = 5                 # ticks (minutes) between mail checks


class Watchers:
    def __init__(self, services, laptop=None, notify=None, clock=time.time):
        self.services, self.laptop, self.notify, self.clock = services, laptop, notify, clock
        self.said: set[str] = set()
        self.cpu_high = 0
        self.seen: dict[str, set[str]] = {}
        self.ticks = 0
        self._stop = threading.Event()

    # -- telling the owner ----------------------------------------------------------------
    def alerts_chat(self) -> int:
        store = self.services.store
        for chat_id, title in store.chats():
            if title == ALERTS_CHAT:
                return chat_id
        chat_id = store.create_chat(ALERTS_CHAT)
        store.rename_chat(chat_id, ALERTS_CHAT, by="user")
        return chat_id

    def alert(self, key: str, text: str) -> bool:
        if key in self.said:
            return False
        self.said.add(key)
        self.services.store.add_message(self.alerts_chat(), "assistant", text, "watcher")
        if self.notify is not None:
            try:
                self.notify(text)
            except Exception:
                pass
        loop = getattr(getattr(self.services, "voice", None), "loop", None)
        focusing = getattr(getattr(self.services, "focus", None), "active", lambda: False)()
        if loop is not None and not loop.muted and not focusing:
            try:
                loop.speaker.say(f"Sir, {text[0].lower() + text[1:]}")
            except Exception:
                pass
        return True

    # -- the checks ------------------------------------------------------------------------
    def check_battery(self) -> None:
        try:
            b = self.laptop.battery()
        except Exception:
            return
        if b["charging"] or b["percent"] >= BATTERY_OK:
            self.said.discard("battery")
        elif b["percent"] < BATTERY_LOW:
            self.alert("battery", f"Battery is at {b['percent']}%. Plug in the charger soon.")

    def check_disk(self) -> None:
        try:
            usage = shutil.disk_usage(self.services.home)
        except OSError:
            return
        used = usage.used / usage.total if usage.total else 0
        if used < DISK_FULL - 0.02:
            self.said.discard("disk")
        elif used >= DISK_FULL:
            free_gb = usage.free / 1024 ** 3
            self.alert("disk", f"Your main drive is {used:.0%} full ({free_gb:.1f} GB left). Ask me to find big files "
                               "to clear.")

    def check_cpu(self) -> None:
        try:
            load = self.laptop.cpu_load()
        except Exception:
            return
        self.cpu_high = self.cpu_high + 1 if load >= CPU_BUSY else 0
        if self.cpu_high == 0:
            self.said.discard("cpu")
        elif self.cpu_high * CHECK_EVERY >= CPU_MINUTES * 60:
            self.alert("cpu", f"The CPU has been over {CPU_BUSY}% for {CPU_MINUTES} minutes. Ask me which apps are "
                              "open if you want to close something.")

    def check_folders(self) -> None:
        policy = self.services.policy
        for w in self.services.settings.watch_folders or []:
            folder = Path(w.get("path", ""))
            try:
                policy.check_read(folder)
                names = {e.name for e in os.scandir(folder) if e.is_file() and not e.name.startswith((".", "~$"))
                         and not e.name.endswith((".crdownload", ".part", ".tmp"))}
            except Exception:
                continue
            key = str(folder)
            if key not in self.seen:                 # first look: remember what is already there
                self.seen[key] = names
                continue
            new = sorted(names - self.seen[key])
            self.seen[key] = names
            if not new:
                continue
            paths = [str(folder / n) for n in new[:NEW_FILES_SHOWN]]
            goal = " ".join(str(w.get("goal") or "").split())
            chat = self.alerts_chat()
            if goal and chat not in self.services.running:
                self.services.store.add_message(chat, "assistant", f"New in {folder.name}: {', '.join(new[:5])}. "
                                                                   f"Doing your goal: {goal}", "watcher")
                self.services.start_task(chat, f"{goal}\nThe new files: {paths}")
            elif not goal:
                self.said.discard(f"folder:{key}")
                self.alert(f"folder:{key}", f"New in {folder.name}: {', '.join(new[:5])}"
                                            + (f" and {len(new) - 5} more" if len(new) > 5 else "") + ".")

    def check_email(self) -> list[str]:
        """New mail from the senders the owner chose: told at once, each email once."""
        from prometheus.agents.email_agent import mailbox

        s, store = self.services.settings, self.services.store
        wanted = [w.lower() for w in getattr(s, "email_alert_senders", None) or [] if w.strip()]
        if not wanted or not getattr(s, "email_address", ""):
            return []
        try:
            unread = mailbox().search(unread=True, limit=20)
        except Exception:
            return []
        seen = set(store.kv_get("email_alert_seen", []))
        told = []
        for m in unread:
            if m["id"] in seen or not any(w in m["from"].lower() for w in wanted):
                continue
            seen.add(m["id"])
            sender = m["from"].split("<")[0].strip().strip('"') or m["from"]
            if self.alert(f"mail:{m['id']}", f"New email from {sender}: {m['subject']}"):
                told.append(m["id"])
        store.kv_set("email_alert_seen", sorted(seen)[-300:])
        return told

    def webhook(self, source: str, text: str) -> None:
        """Something outside called Prometheus's webhook address: say it (its text is shown, never obeyed)."""
        self.alert(f"hook:{time.time()}", f"{source}: {' '.join(text.split())[:300]}")

    def tick(self) -> None:
        s = self.services.settings
        self.ticks += 1
        if self.ticks % EMAIL_EVERY == 1 or EMAIL_EVERY == 1:
            self.check_email()
        if s.watch_battery and self.laptop is not None:
            self.check_battery()
        if s.watch_disk:
            self.check_disk()
        if s.watch_cpu and self.laptop is not None:
            self.check_cpu()
        if s.watch_folders:
            self.check_folders()

    def loop(self) -> None:
        while not self._stop.wait(CHECK_EVERY):
            try:
                self.tick()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-watchers", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
