"""Turns a recorded lecture into a transcript, notes, revision slides and flashcards (in the background)."""
from __future__ import annotations

from pathlib import Path

from prometheus import lectures
from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.safety.paths import PathBlocked

SERVICES = None


@register
class LectureAgent(Agent):
    name = "lecture"
    description = ("Turns a recorded class (audio or video file: mp3, m4a, wav, mp4, mkv…) into a transcript, notes "
                   "PDF, revision slides and flashcards, on the laptop; English, Hindi and Hinglish. Inputs: path "
                   "(the recording), slides ('false' to skip), cards ('false' to skip). It runs in the background "
                   "and posts here when done.")
    tools = {"read_files", "call_model"}
    accepts_private = True
    required_inputs = ("path",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        if SERVICES is None:
            return StepResult("failed", "Lecture notes aren't set up.")
        try:
            path = ctx.policy.check_read(Path(str(inputs.get("path") or "")).expanduser())
        except PathBlocked as e:
            return StepResult("failed", f"I can't use that file: {e}")
        if not path.is_file() or path.suffix.lower() not in lectures.AUDIO:
            return StepResult("failed", f"{path.name} isn't an audio or video file I can listen to.")
        size_mb = path.stat().st_size / 1024 ** 2
        lectures.LectureJob(SERVICES, path, ctx.chat_id(), ctx.task_id,
                            slides=str(inputs.get("slides", "true")).lower() != "false",
                            cards=str(inputs.get("cards", "true")).lower() != "false").start()
        return StepResult("done", f"Started on {path.name} ({size_mb:.0f} MB). Listening to a lecture takes a while "
                                  "(very roughly a quarter to half of its length); I'll post the transcript, notes, "
                                  "slides and flashcards here when they're ready.")
