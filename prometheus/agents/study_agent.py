"""The study coach: flashcards from notes, quizzes, spaced review and Anki export."""
from __future__ import annotations

from pathlib import Path

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.agents.textutil import extract_text
from prometheus.safety.paths import PathBlocked

STUDY = None       # the app's Study


@register
class StudyAgent(Agent):
    name = "study"
    description = ("Study coach. Actions: make (paths = the owner's notes, PDFs or slides, or text = notes typed in "
                   "the chat; deck = subject or chapter name; count = how many cards, default 12), quiz (deck, or "
                   "empty for everything due), decks (what decks exist and what's due), add (deck, question, "
                   "answer), export (deck: a file for Anki). Quizzes run in this chat: the owner's next messages "
                   "are their answers.")
    tools = {"read_files", "call_model"}
    accepts_private = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        if STUDY is None:
            return StepResult("failed", "The study coach isn't set up.")
        action = str(inputs.get("action") or "").strip().lower()
        deck = str(inputs.get("deck") or "").strip()
        if action == "make":
            raw = inputs.get("paths") or inputs.get("path") or []
            paths = [raw] if isinstance(raw, str) else list(raw)
            texts = []
            for p in paths:
                try:
                    path = ctx.policy.check_read(Path(str(p)).expanduser())
                except PathBlocked as e:
                    return StepResult("failed", f"I can't read {p}: {e}")
                texts.append((path.name, extract_text(path, limit=60_000)))
            if inputs.get("text"):
                texts.append(("notes from the chat", str(inputs["text"])))
            if not any(t.strip() for _, t in texts):
                return StepResult("failed", "There were no notes to make cards from (or the file has no text).")
            try:
                count = max(3, min(40, int(inputs.get("count") or 12)))
            except ValueError:
                count = 12
            deck = deck or (Path(texts[0][0]).stem if paths else "general")
            made = []
            for name, text in texts:
                made += STUDY.make(text, deck, source=name, count=count)
            if not made:
                return StepResult("failed", "I couldn't make cards from that. Try a smaller part of the notes.")
            return StepResult("done", f"Made {len(made)} flashcards in the deck '{deck}'. Say \"quiz me on "
                                      f"{deck}\" when you're ready.", {"made_cards": len(made)})
        if action == "quiz":
            return StepResult("done", STUDY.start_quiz(ctx.chat_id(), deck))
        if action == "decks":
            decks = STUDY.decks()
            if not decks:
                return StepResult("done", "No flashcards yet. Say \"make flashcards from <your notes file>\".")
            return StepResult("done", "\n".join(f"- {d['deck']}: {d['n']} cards, {d['due'] or 0} due" for d in decks))
        if action == "add":
            cid = STUDY.add(deck or "general", str(inputs.get("question") or ""), str(inputs.get("answer") or ""))
            return StepResult("done" if cid else "failed", "Card added." if cid else "That card is empty or already there.")
        if action == "export":
            if not deck:
                return StepResult("failed", "Which deck should I export?")
            path = STUDY.export_anki(deck, ctx.home / "Documents" / "Prometheus Study")
            ctx.store.add_output(ctx.task_id, str(path), "created")
            return StepResult("done", f"Saved {path.name}. In Anki: File → Import, then pick this file.",
                              {"made": [str(path)]})
        return StepResult("failed", "I can: make, quiz, decks, add, export.")
