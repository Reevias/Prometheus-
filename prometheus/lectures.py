"""Lecture to notes: a recorded class (audio or video) → transcript, notes PDF, revision slides and flashcards.

Speech to text runs on the laptop (Whisper "small", which understands English, Hindi and Hinglish; downloaded once,
about 460 MB). A one-hour lecture takes very roughly 15 to 30 minutes on a laptop without a graphics card, so it
works in the background and posts in the chat when it's done. Notes are written by the local AI unless the owner
let Claude help with study material (Settings → Study coach).
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

AUDIO = {".mp3", ".m4a", ".wav", ".aac", ".ogg", ".opus", ".flac", ".wma", ".mp4", ".mkv", ".webm", ".mov", ".avi",
         ".3gp"}
MODEL = "small"
PART_CHARS = 9000
NOTES_PROMPT = ("Write clear study notes from this part of a lecture transcript: Markdown with ## headings for "
                "each topic, short bullet points, key definitions in bold, formulas and examples kept, and a "
                "'Key points' list at the end. Only what the lecturer said; fix obvious speech-to-text slips.")


def decode(path: Path):
    """Any audio or video file → 16 kHz mono samples. Done here (not by faster-whisper) because newer PyAV
    versions changed a call faster-whisper relies on."""
    import av
    import numpy as np

    chunks = []
    with av.open(str(path)) as container:
        stream = next((s for s in container.streams if s.type == "audio"), None)
        if stream is None:
            raise ValueError("This file has no sound.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray().reshape(-1))
        for out in resampler.resample(None):
            chunks.append(out.to_ndarray().reshape(-1))
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0


def transcribe(path: Path, models_folder: Path, progress=lambda text: None, model: str = MODEL) -> str:
    from faster_whisper import WhisperModel
    from faster_whisper.utils import download_model

    folder = Path(models_folder) / "whisper" / model
    if not (folder / "model.bin").exists():
        progress("Downloading the lecture speech model (460 MB, once)…")
        folder.mkdir(parents=True, exist_ok=True)
        download_model(model, output_dir=str(folder))
    import os

    threads = max(2, (os.cpu_count() or 4) - 1)
    whisper = WhisperModel(str(folder), device="cpu", compute_type="int8", cpu_threads=threads)
    segments, info = whisper.transcribe(decode(path), language=None, beam_size=2, vad_filter=True,
                                        condition_on_previous_text=True,
                                        initial_prompt="A class lecture. Lecture notes, examples and formulas.")
    total = getattr(info, "duration", 0) or 0
    lines, last = [], time.monotonic()
    for seg in segments:
        lines.append(seg.text.strip())
        if total and time.monotonic() - last > 20:
            progress(f"Transcribing… {min(99, int(100 * seg.end / total))}%")
            last = time.monotonic()
    text = " ".join(lines)
    return re.sub(r"(?<=[.?!])\s+", "\n", text)        # one sentence per line reads better


def notes_from(transcript: str, router, route: str, title: str) -> str:
    from prometheus.agents.base import UNTRUSTED_RULE, untrusted

    parts, cur = [], ""
    for line in transcript.splitlines():
        if len(cur) + len(line) > PART_CHARS and cur:
            parts.append(cur)
            cur = ""
        cur += line + "\n"
    if cur.strip():
        parts.append(cur)
    out = [f"# {title}"]
    for i, part in enumerate(parts, 1):
        reply = router.complete(route, [{"role": "user", "content": f"{NOTES_PROMPT}\n\nPart {i} of {len(parts)}:\n"
                                         + untrusted(part, "lecture transcript")}],
                                "You turn lecture transcripts into excellent student notes. " + UNTRUSTED_RULE,
                                max_tokens=2500)
        out.append(reply.text.strip())
    return "\n\n".join(out)


class LectureJob:
    """Runs one lecture in the background and posts each step in the chat."""

    def __init__(self, services, path: Path, chat_id: int, task_id: int, slides: bool = True, cards: bool = True,
                 transcriber=None):
        self.services, self.path, self.chat_id, self.task_id = services, Path(path), chat_id, task_id
        self.slides, self.cards = slides, cards
        self.transcriber = transcriber or transcribe

    def say(self, text: str) -> None:
        self.services.store.add_message(self.chat_id, "assistant", text, "lecture", private=True)

    def run(self) -> dict:
        from prometheus.actions.office import markdown_pptx
        from prometheus.actions.pdf import markdown_pdf
        from prometheus.config import data_dir

        s, store = self.services, self.services.store
        name = re.sub(r"[^\w\s-]", " ", self.path.stem).strip()[:60] or "Lecture"
        folder = Path(s.home) / "Documents" / "Prometheus Lectures" / name
        folder.mkdir(parents=True, exist_ok=True)
        made = {}
        try:
            text = self.transcriber(self.path, data_dir() / "voice", progress=lambda m: store.kv_set("lecture_progress", m))
        except ImportError:
            self.say('Lecture notes need the voice parts. To fix it, run the installer again (irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/install-windows.ps1 | iex)')
            return made
        except Exception as e:
            self.say(f"I couldn't turn {self.path.name} into text ({type(e).__name__}). Is it an audio or video file?")
            return made
        if not text.strip():
            self.say(f"I didn't hear any speech in {self.path.name}.")
            return made
        (folder / "Transcript.txt").write_text(text, encoding="utf-8")
        made["transcript"] = folder / "Transcript.txt"
        route = "chat" if getattr(s.settings, "study_cloud", False) else "private"
        try:
            notes = notes_from(text, s.router, route, name)
        except Exception as e:
            notes = ""
            self.say(f"The transcript is ready, but writing notes failed ({type(e).__name__}): is the local AI on?")
        if notes:
            (folder / "Notes.pdf").write_bytes(markdown_pdf(notes, name))
            made["notes"] = folder / "Notes.pdf"
            if self.slides:
                deck = re.sub(r"^# .*$", f"# {name}\nRevision slides", notes, count=1, flags=re.M)
                (folder / "Revision slides.pptx").write_bytes(markdown_pptx(deck, name))
                made["slides"] = folder / "Revision slides.pptx"
            if self.cards:
                from prometheus.agents import study_agent

                if study_agent.STUDY is not None:
                    try:
                        made["cards"] = len(study_agent.STUDY.make(notes, name, source=self.path.name, count=15))
                    except Exception:
                        pass
        for key, p in made.items():
            if isinstance(p, Path):
                store.add_output(self.task_id, str(p), "created")
        bits = [f"{self.path.name} is done:"]
        bits += [f"- {k.title()}: {v.name}" for k, v in made.items() if isinstance(v, Path)]
        if made.get("cards"):
            bits.append(f"- {made['cards']} flashcards in the deck '{name.lower()}' (say \"quiz me on {name.lower()}\")")
        bits.append(f"All in {folder}.")
        self.say("\n".join(bits))
        try:
            s.notify("task", f"Lecture notes ready: {name}")
        except Exception:
            pass
        return made

    def start(self) -> threading.Thread:
        t = threading.Thread(target=self.run, name="prometheus-lecture", daemon=True)
        t.start()
        return t
