"""Chat names: a quick one from the first message, then a better one from the planner."""
from __future__ import annotations

import re

PLACEHOLDERS = ("New chat", "First chat")
MAX_WORDS = 7
MAX_CHARS = 60
FILLER = re.compile(
    r"^(?:(?:hey|hi|hello|yo|ok(?:ay)?|so|um+|uh+|bro|dude|prometheus|jarvis)\b[\s,!.]*|"
    r"(?:can|could|would|will) you(?: please)?\s+|please\s+|pls\s+|i (?:want|need|would like) (?:you )?to\s+|"
    r"i want\s+|help me(?: to)?\s+|tell me\s+|let'?s\s+)+", re.I)


def _tidy(text: str) -> str:
    text = re.sub(r"https?://\S+", "", text or "")
    text = re.sub(r"[\r\n\t]+", " ", text)
    text = re.sub(r"[\"'`*_#<>\[\]{}|]+", "", text)
    return " ".join(text.split()).strip(" .,:;!?-")


def _cut(text: str) -> str:
    words = text.split()
    out = " ".join(words[:MAX_WORDS])
    if len(out) > MAX_CHARS:
        out = out[:MAX_CHARS].rsplit(" ", 1)[0]
    out = out.strip(" .,:;!?-")
    return out[:1].upper() + out[1:] if out else ""


def quick_title(first_message: str) -> str:
    """Name a chat from its first message without a model: drop greetings and 'can you', keep the subject."""
    text = _tidy(first_message)
    shorter = FILLER.sub("", text).strip()
    return _cut(shorter or text) or "New chat"


def clean_title(raw: str) -> str:
    """Make a model-written name safe and short. Returns '' if nothing usable is left."""
    text = _tidy(raw)
    text = re.sub(r"^(?:title|chat|name)\s*:\s*", "", text, flags=re.I)
    return _cut(text)
