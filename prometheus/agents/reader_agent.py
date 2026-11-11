"""Read-aloud: reads a PDF chapter, notes or text out loud, with voice controls."""
from __future__ import annotations

from pathlib import Path

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.agents.textutil import extract_text
from prometheus.safety.paths import PathBlocked
from prometheus.voice import reader

ACTIONS = {"pause": "pause", "continue": "resume", "resume": "resume", "back": "back", "skip": "skip",
           "explain": "explain", "faster": "faster", "slower": "slower", "stop": "stop", "status": "status"}


@register
class ReaderAgent(Agent):
    name = "reader"
    description = ("Reads out loud in Prometheus's voice. Actions: read (path = a PDF, Word or text file, pages = "
                   "e.g. '10-25' for a chapter of a PDF, or text = what to read), pause, continue, back, skip, "
                   "explain (explains the part being read in simple words), faster, slower, stop. The part being "
                   "read is highlighted on screen.")
    tools = {"read_files", "call_model"}
    accepts_private = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        r = reader.CURRENT
        if r is None:
            return StepResult("failed", "Read-aloud isn't set up.")
        action = str(inputs.get("action") or "").lower()
        if action == "read":
            text, title, private = str(inputs.get("text") or ""), "your text", False
            raw = str(inputs.get("path") or "").strip()
            if raw:
                try:
                    path = ctx.policy.check_read(Path(raw).expanduser())
                except PathBlocked as e:
                    return StepResult("failed", f"I can't read that file: {e}")
                pages = str(inputs.get("pages") or "")
                text = reader.pdf_pages(path, pages) if path.suffix.lower() == ".pdf" and pages else \
                    extract_text(path, limit=400_000)
                title, private = path.stem + (f", pages {pages}" if pages else ""), True
            if not r.load(text, title, private):
                return StepResult("failed", "There's no readable text there.")
            return StepResult("done", r.start() + " Say \"Hello Prometheus, pause\" (or continue, go back, explain "
                                                  "that again, read faster, stop reading) any time.")
        act = ACTIONS.get(action)
        if act is None:
            return StepResult("failed", "I can: read, pause, continue, back, skip, explain, faster, slower, stop.")
        out = {"pause": r.pause, "resume": r.resume, "back": lambda: r.move(-1), "skip": lambda: r.move(1),
               "explain": r.explain, "faster": lambda: r.set_speed(True), "slower": lambda: r.set_speed(False),
               "stop": r.stop, "status": lambda: f"{r.state}: part {r.status()['index'] + 1} of {len(r.parts)}"}[act]()
        return StepResult("done", out)
