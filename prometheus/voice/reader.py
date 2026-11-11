"""Read-aloud mode: Prometheus reads a PDF chapter or notes out loud in its voice.

Say "Hello Prometheus, pause / continue / go back / skip / explain that part again / read faster / read slower /
stop reading". The paragraph being read is highlighted on screen, with buttons for the same things.
"Explain that part again" asks the AI to explain the current paragraph simply, says it, then carries on.
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

CURRENT = None          # the app's Reader
PARA_CHARS = 600
SPEEDS = (0.7, 0.8, 0.9, 1.0, 1.1, 1.2, 1.35, 1.5)


def paragraphs(text: str) -> list[str]:
    """Readable chunks: real paragraphs where the text has them, otherwise groups of sentences."""
    text = re.sub(r"-\n(?=[a-z])", "", text or "")                 # words split across PDF lines
    blocks = [" ".join(b.split()) for b in re.split(r"\n\s*\n", text) if b.strip()]
    if len(blocks) <= 2 and len(text) > PARA_CHARS * 2:
        blocks = [" ".join(text.split())]
    out = []
    for b in blocks:
        if len(b) <= PARA_CHARS:
            out.append(b)
            continue
        cur = ""
        for sent in re.split(r"(?<=[.!?])\s+", b):
            if len(cur) + len(sent) > PARA_CHARS and cur:
                out.append(cur.strip())
                cur = ""
            cur += sent + " "
        if cur.strip():
            out.append(cur.strip())
    return [p for p in out if re.search(r"[A-Za-z]{2}", p)]


def pdf_pages(path: Path, pages: str) -> str:
    """'10-25' or '3' → the text of those pages."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    m = re.fullmatch(r"\s*(\d+)\s*(?:-|to)\s*(\d+)\s*", pages or "") or re.fullmatch(r"\s*(\d+)\s*", pages or "")
    if not m:
        start, end = 1, len(reader.pages)
    else:
        start = int(m.group(1))
        end = int(m.group(2)) if m.lastindex and m.lastindex > 1 else start
    end = min(end, len(reader.pages))
    return "\n\n".join(reader.pages[i].extract_text() or "" for i in range(max(0, start - 1), end))


class Reader:
    def __init__(self, services, speaker=None):
        self.services = services
        self._speaker = speaker
        self.title, self.parts, self.index = "", [], 0
        self.state = "off"                    # off | reading | paused | explaining | done
        self.private = True
        self._stop = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def speaker(self):
        if self._speaker is not None:
            return self._speaker
        loop = getattr(getattr(self.services, "voice", None), "loop", None)
        return getattr(loop, "speaker", None)

    @property
    def speed(self) -> float:
        return float(getattr(self.services.settings, "read_speed", 1.0) or 1.0)

    def load(self, text: str, title: str, private: bool = True) -> int:
        self.stop()
        self.title, self.parts, self.index, self.private = title[:120], paragraphs(text), 0, private
        return len(self.parts)

    # -- playing ---------------------------------------------------------------------------
    def start(self) -> str:
        if not self.parts:
            return "There's nothing to read."
        if self.speaker() is None:
            return "Turn voice on first (Settings → Voice), then ask me to read again."
        self._stop.clear()
        self.state = "reading"
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="prometheus-reader", daemon=True)
            self._thread.start()
        return f"Reading {self.title} ({len(self.parts)} parts)."

    def _run(self) -> None:
        while self.index < len(self.parts) and self.state == "reading":
            spk = self.speaker()
            if spk is None:
                self.state = "paused"
                return
            i = self.index
            try:
                finished = spk.say(self.parts[i], speed=self.speed, stop=self._stop)
            except TypeError:                          # a speaker without speed control
                spk.say(self.parts[i])
                finished = not self._stop.is_set()
            if self._stop.is_set() or not finished:
                return                                 # paused, stopped, moved: whoever did it decides what's next
            with self._lock:
                if self.index == i:
                    self.index += 1
        if self.index >= len(self.parts) and self.state == "reading":
            self.state = "done"

    def _interrupt(self) -> None:
        self._stop.set()
        spk = self.speaker()
        if spk is not None and hasattr(spk, "stop"):
            spk.stop()
        if self._thread is not None:
            self._thread.join(timeout=3)

    def pause(self) -> str:
        if self.state != "reading":
            return "I'm not reading right now."
        self.state = "paused"
        self._interrupt()
        return "Paused. Say \"continue\" when you're ready."

    def resume(self) -> str:
        if self.state not in ("paused", "done") or not self.parts:
            return "There's nothing paused to continue."
        if self.state == "done":
            return "That was the end."
        return self.start()

    def move(self, steps: int) -> str:
        was = self.state
        self._interrupt()
        with self._lock:
            self.index = max(0, min(len(self.parts) - 1, self.index + steps))
        if was in ("reading", "explaining"):
            self.state = "paused"
            return self.start()
        return f"Moved to part {self.index + 1} of {len(self.parts)}."

    def set_speed(self, faster: bool) -> str:
        from prometheus.config import save_settings

        cur = min(SPEEDS, key=lambda v: abs(v - self.speed))
        i = SPEEDS.index(cur) + (1 if faster else -1)
        new = SPEEDS[max(0, min(len(SPEEDS) - 1, i))]
        self.services.settings.read_speed = new
        try:
            save_settings(self.services.settings)
        except Exception:
            pass
        return f"Reading speed {new:g}×" + (" (the next sentence)." if self.state == "reading" else ".")

    def stop(self) -> str:
        if self.state == "off":
            return "I'm not reading."
        self.state = "off"
        self._interrupt()
        return "Stopped reading."

    def explain(self) -> str:
        """Explain the current paragraph simply, say it, then carry on reading."""
        if not self.parts:
            return "I'm not reading anything."
        was = self.state
        self._interrupt()
        self.state = "explaining"
        part = self.parts[min(self.index, len(self.parts) - 1)]
        from prometheus.agents.base import UNTRUSTED_RULE, untrusted

        route = "chat" if (not self.private or getattr(self.services.settings, "study_cloud", False)) else "private"
        try:
            reply = self.services.router.complete(route, [{"role": "user", "content": (
                "Explain this part of what the student is reading in simple words, in 3 to 5 short sentences, with "
                "an everyday example if it helps.\n" + untrusted(part, self.title))}],
                "You are a patient tutor explaining a textbook out loud. " + UNTRUSTED_RULE, max_tokens=400)
            explanation = reply.text.strip()
        except Exception as e:
            explanation = f"I couldn't explain it right now ({type(e).__name__})."
        spk = self.speaker()
        if spk is not None:
            try:
                spk.say(explanation)
            except Exception:
                pass
        self.state = "paused"
        if was in ("reading", "paused", "explaining"):
            self.start()
        return explanation

    def status(self) -> dict:
        i = min(self.index, max(0, len(self.parts) - 1))
        return {"state": self.state, "title": self.title, "index": i, "total": len(self.parts),
                "before": self.parts[i - 1] if i > 0 and self.parts else "",
                "now": self.parts[i] if self.parts else "",
                "after": self.parts[i + 1] if i + 1 < len(self.parts) else "", "speed": self.speed}

    def active(self) -> bool:
        return self.state in ("reading", "paused", "explaining")
