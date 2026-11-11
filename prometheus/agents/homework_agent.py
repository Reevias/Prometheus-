"""Homework checker: a photo of a worked answer is checked step by step. It shows WHERE it went wrong with a hint,
not the answer.

Reading handwriting and maths needs an AI that sees images, so the photo the owner chose (and only that photo)
is sent to Claude, as the owner agreed. If notes are given, the local AI compares the method with them on the
laptop; the notes themselves are never sent.
"""
from __future__ import annotations

import base64
import io
import json
from pathlib import Path

from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, register, untrusted
from prometheus.agents.textutil import extract_text
from prometheus.safety.paths import PathBlocked

IMAGES = {".jpg", ".jpeg", ".png", ".webp", ".heic"}
MAX_SIDE = 1568
CHECK_SCHEMA = {"type": "object", "properties": {
    "question": {"type": "string"},
    "steps": {"type": "array", "items": {"type": "object", "properties": {
        "written": {"type": "string"}, "ok": {"type": "boolean"}, "comment": {"type": "string"}},
        "required": ["written", "ok", "comment"], "additionalProperties": False}},
    "first_mistake": {"type": "integer"}, "hint": {"type": "string"}, "overall": {"type": "string"}},
    "required": ["question", "steps", "first_mistake", "hint", "overall"], "additionalProperties": False}
PROMPT = ("This photo is a student's worked answer. Read every step exactly as written (handwriting and maths). "
          "Check each step. For each, say if it is correct and, if not, what kind of mistake it is (sign, unit, "
          "formula, arithmetic, concept) WITHOUT giving the correct working or the final answer. first_mistake is "
          "the index of the first wrong step (-1 if all correct). hint: one nudge that helps them find and fix the "
          "first mistake themselves. overall: one encouraging sentence.")


def image_block(path: Path) -> dict:
    data, mime = path.read_bytes(), "image/jpeg"
    try:
        from PIL import Image, ImageOps

        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=88)
        data = out.getvalue()
    except Exception:
        mime = {".png": "image/png", ".webp": "image/webp"}.get(path.suffix.lower(), "image/jpeg")
    return {"type": "image", "source": {"type": "base64", "media_type": mime,
                                        "data": base64.b64encode(data).decode()}}


def newest_photo(home: Path) -> Path | None:
    found = []
    for folder in ("Pictures", "Downloads", "Desktop", "Pictures/Camera Roll", "OneDrive/Pictures"):
        d = home / folder
        if d.is_dir():
            found += [p for p in d.iterdir() if p.suffix.lower() in IMAGES and p.is_file()]
    return max(found, key=lambda p: p.stat().st_mtime) if found else None


@register
class HomeworkAgent(Agent):
    name = "homework"
    description = ("Checks a photo of the owner's worked homework answer step by step and shows where it went wrong "
                   "with a hint, never the answer. Inputs: path (the photo, or 'latest' for the newest photo), "
                   "question (the problem, if not in the photo), notes (optional paths to their notes for the "
                   "method). The photo is sent to Claude to read; notes stay on the laptop.")
    tools = {"read_files", "call_model"}
    accepts_private = True
    private_outputs = True      # may quote the owner's notes; also keeps a cloud model from "helpfully" solving it
    required_inputs = ("path",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        ctx.use(self.name, "call_model")
        raw = str(inputs.get("path") or "").strip()
        path = newest_photo(ctx.home) if raw.lower() in ("latest", "newest", "last") else Path(raw).expanduser()
        if path is None:
            return StepResult("failed", "I couldn't find a photo. Save it in Pictures or Downloads, or tell me where.")
        try:
            path = ctx.policy.check_read(path)
        except PathBlocked as e:
            return StepResult("failed", f"I can't use that photo: {e}")
        if path.suffix.lower() not in IMAGES:
            return StepResult("failed", f"{path.name} isn't a photo.")
        router = ctx.router
        if "vision" not in router.routes:
            router.routes["vision"] = ["anthropic"]
        question = str(inputs.get("question") or "")
        content = [image_block(path), {"type": "text", "text": PROMPT + (
            "\n\nThe problem: " + question if question else "")}]
        reply = router.complete("vision", [{"role": "user", "content": content}],
                                "You are a kind, exact tutor who never gives away answers. " + UNTRUSTED_RULE,
                                json_schema=CHECK_SCHEMA, max_tokens=2500)
        try:
            data = json.loads(reply.text)
        except ValueError:
            return StepResult("failed", "I couldn't read the check properly. Try a clearer, straighter photo.")
        lines = [f"**{data.get('question') or 'Your answer'}**"]
        for i, st in enumerate(data.get("steps", []), 1):
            mark = "✓" if st.get("ok") else "✗"
            lines.append(f"{i}. {mark} {st.get('written', '')}" + ("" if st.get("ok") else f" — {st.get('comment')}"))
        first = int(data.get("first_mistake", -1))
        if first >= 0:
            lines.append(f"\nFirst thing to fix: step {first + 1}. Hint: {data.get('hint', '')}")
        else:
            lines.append("\nEvery step looks right. 🎉")
        lines.append(data.get("overall", ""))
        notes_raw = inputs.get("notes") or []
        notes = [notes_raw] if isinstance(notes_raw, str) else list(notes_raw)
        if notes and first >= 0:
            text = ""
            for n in notes[:3]:
                try:
                    text += extract_text(ctx.policy.check_read(Path(str(n)).expanduser()), limit=20_000)
                except PathBlocked:
                    continue
            if text.strip():
                try:
                    steps = "\n".join(s.get("written", "") for s in data.get("steps", []))
                    local = router.complete("private", [{"role": "user", "content": (
                        f"A student's working:\n{steps}\nTheir first mistake is in step {first + 1}. In one or two "
                        "sentences, point to the rule or method in their notes that this step should follow, "
                        "without solving it.\n" + untrusted(text[:12000], "notes"))}],
                        "You help students find the right method in their own notes. " + UNTRUSTED_RULE,
                        max_tokens=300)
                    lines.append(f"\nFrom your notes: {local.text.strip()}")
                except Exception:
                    pass
        return StepResult("done", "\n".join(l for l in lines if l is not None))
