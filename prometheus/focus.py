"""Focus mode: "focus for 2 hours", "focus until 6pm", "focus mode on" (until you say stop), "stop focus".

While focusing: Prometheus holds its own alerts and notifications (reminders you set still ring) and stays quiet,
Windows Do Not Disturb is on (banners from other apps stop), the distracting apps chosen in Settings are closed
at the start, and with pomodoro on a voice says when to take a break and when to go back. At the end you get a
short summary of what came in.
"""
from __future__ import annotations

import json
import re
import threading
import time

CHECK = 15
LONGEST = 12 * 3600


def parse_length(text: str, now: float) -> float | None:
    """Seconds to focus for: 'for 2 hours', '90 minutes', 'until 6pm', 'for an hour and a half'. None = until stopped."""
    t = " ".join((text or "").lower().split())
    if re.search(r"\buntil\b|\btill\b|\btak\b", t):
        from prometheus.reminders import WhenError, parse_when

        try:
            due = parse_when("at " + re.split(r"\buntil\b|\btill\b|\btak\b", t)[-1].strip(), now).due
            return max(60.0, min(LONGEST, due - now))
        except WhenError:
            return None
    total = 0.0
    words = {"an": 1, "a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "half an": 0.5,
             "half": 0.5, "ten": 10, "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45, "forty": 40}
    for num, unit in re.findall(r"(\d+(?:\.\d+)?|half an|forty five|an|a|one|two|three|four|five|six|ten|fifteen|"
                                r"twenty|thirty|forty|half)\s*(hours?|hrs?|h\b|minutes?|mins?|m\b|ghant[ae]|minat)", t):
        n = float(num) if num[0].isdigit() else words[num]
        total += n * (3600 if unit.startswith(("h", "ghant")) else 60)
    if re.search(r"\band a half\b", t) and total:
        total += 1800
    return min(LONGEST, total) if total else None


class Focus:
    def __init__(self, services, laptop=None, clock=time.time, say=None):
        self.services, self.laptop, self.clock = services, laptop, clock
        self.say = say                              # speaks a short line (break reminders); None = voice's speaker
        self._stop = threading.Event()
        self._thread = None

    # -- state (kept in memory.db so a restart doesn't forget it) --------------------------
    def state(self) -> dict | None:
        raw = self.services.store.kv_get("focus")
        if not raw:
            return None
        data = json.loads(raw)
        return data if data.get("active") else None

    def _save(self, data: dict | None) -> None:
        self.services.store.kv_set("focus", json.dumps(data or {"active": False}))

    def active(self) -> bool:
        s = self.state()
        return bool(s) and (s["until"] is None or self.clock() < s["until"])

    def hold(self, text: str) -> bool:
        """Called instead of showing a notification. True when it was held for later."""
        s = self.state()
        if not s or not self.active():
            return False
        s["held"].append([self.clock(), text[:200]])
        s["held"] = s["held"][-50:]
        self._save(s)
        return True

    # -- starting and stopping ------------------------------------------------------------------
    def start(self, seconds: float | None) -> str:
        s = self.services.settings
        now = self.clock()
        work, rest = int(getattr(s, "focus_work", 25) or 25), int(getattr(s, "focus_break", 5) or 5)
        data = {"active": True, "started": now, "until": now + seconds if seconds else None, "held": [],
                "closed": [], "pomodoro": bool(getattr(s, "focus_pomodoro", True)), "work": work, "rest": rest,
                "phase": "work", "phase_end": now + work * 60, "dnd": False}
        closed = []
        if self.laptop is not None:
            for app in getattr(s, "focus_apps", []) or []:
                try:
                    if "Closed" in self.laptop.app_close(app):
                        closed.append(app)
                except Exception:
                    pass
            if getattr(s, "focus_dnd", True):
                try:
                    self.laptop.do_not_disturb(True)
                    data["dnd"] = True
                except Exception:
                    pass
        data["closed"] = closed
        self._save(data)
        length = _length(seconds)
        bits = [f"Focus on {length}." if seconds else "Focus on until you say stop."]
        if data["dnd"]:
            bits.append("Do not disturb is on.")
        if closed:
            bits.append("Closed " + ", ".join(closed) + ".")
        if data["pomodoro"]:
            bits.append(f"Breaks: {rest} minutes every {work}.")
        bits.append("I'll hold my alerts until you're done.")
        return " ".join(bits)

    def stop(self, reason: str = "") -> str:
        data = self.state()
        if not data:
            return "Focus mode isn't on."
        if data.get("dnd") and self.laptop is not None:
            try:
                self.laptop.do_not_disturb(False)
            except Exception:
                pass
        self._save(None)
        took = _length(self.clock() - data["started"])
        held = data.get("held", [])
        text = (reason or "Focus done.") + f" You focused for {took}."
        if held:
            text += f" While you were focused ({len(held)}):\n" + "\n".join(f"- {t}" for _, t in held[-10:])
        else:
            text += " Nothing came in."
        return text

    # -- the clock ---------------------------------------------------------------------------------
    def tick(self) -> str:
        data = self.state()
        if not data:
            return ""
        now = self.clock()
        if data["until"] is not None and now >= data["until"]:
            text = self.stop("Time's up, focus is over.")
            self._tell(text, speak="Time's up, sir. Focus is over.")
            return "ended"
        if data["pomodoro"] and now >= data["phase_end"]:
            if data["phase"] == "work":
                data["phase"], data["phase_end"] = "break", now + data["rest"] * 60
                line = f"Break time, sir. {data['rest']} minutes."
            else:
                data["phase"], data["phase_end"] = "work", now + data["work"] * 60
                line = "Break's over, sir. Back to work."
            self._save(data)
            self._speak(line)
            return data["phase"]
        return ""

    def _speak(self, line: str) -> None:
        say = self.say
        if say is None:
            loop = getattr(getattr(self.services, "voice", None), "loop", None)
            say = (getattr(loop, "announce", None) or loop.speaker.say) if loop is not None and not loop.muted else None
        if say is not None:
            try:
                say(line)
            except Exception:
                pass

    def _tell(self, text: str, speak: str = "") -> None:
        watchers = getattr(self.services, "watchers", None)
        if watchers is not None:
            self.services.store.add_message(watchers.alerts_chat(), "assistant", text, "focus")
        if speak:
            self._speak(speak)

    def loop(self) -> None:
        while not self._stop.wait(CHECK):
            try:
                self.tick()
            except Exception:
                pass

    def start_clock(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-focus", daemon=True).start()

    def stop_clock(self) -> None:
        self._stop.set()


def _length(seconds: float | None) -> str:
    if not seconds:
        return "a while"
    mins = max(1, round(seconds / 60))
    if mins < 60:
        return f"{mins} minute{'s' if mins != 1 else ''}"
    h, m = divmod(mins, 60)
    return f"{h} hour{'s' if h != 1 else ''}" + (f" {m} minutes" if m else "")
