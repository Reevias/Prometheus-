"""Turning speech into a command, and an answer into something worth hearing."""
from __future__ import annotations

import re

# How Whisper writes the name when it mishears it, seen in tests: "Prometheus", "Permitheus", "Promethece",
# "Prumi Theus", "Promethius". The pattern needs the "m…th" core, so "promising", "problem" and "promote" never match.
NAME = re.compile(r"^p[re]{0,2}[aeiou]?m[aeiouy]{1,2}th[aeiouy]{0,3}(?:s|z|ce|se)$")
SPEAK_CHARS = 320


def _words(text: str) -> list[tuple[str, int, int]]:
    return [(m.group().lower(), m.start(), m.end()) for m in re.finditer(r"[A-Za-z']+", text or "")]


LEARNED: dict | None = None     # the owner's trained profile (voice/training.py): how the name is heard for them


def set_learned(profile: dict | None) -> None:
    global LEARNED
    LEARNED = profile if profile and profile.get("aliases") else None


def find_wake(text: str, learned=None) -> tuple[int, int] | None:
    """Where the name is in the text (start, end), allowing for how speech-to-text misspells it.
    learned: None uses the trained profile, False ignores it, a dict uses that one."""
    profile = LEARNED if learned is None else (learned or None)
    aliases = set(profile.get("aliases", [])) if profile else set()
    greetings = set(profile.get("greetings", [])) if profile else set()
    words = _words(text)
    for i, (w, start, end) in enumerate(words):
        w = w.replace("'", "")
        if NAME.match(w):
            return start, end
        two = w + words[i + 1][0].replace("'", "") if i + 1 < len(words) else ""
        if two and NAME.match(two):                                                   # "Prumi Theus"
            return start, words[i + 1][2]
        # a spelling learned in training counts only right after a greeting ("hello permit us")
        if aliases and i > 0 and re.sub(r"[^a-z]", "", words[i - 1][0]) in greetings:
            if re.sub(r"[^a-z]", "", two) in aliases:
                return start, words[i + 1][2]
            if re.sub(r"[^a-z]", "", w) in aliases:
                return start, end
    return None


def has_wake_word(text: str, learned=None) -> bool:
    """True when the name was said ("Hello Prometheus", "Prometheus", or a common mishearing of it)."""
    return find_wake(text, learned) is not None


def scrub_wake(text: str) -> str:
    """Before speaking text that came from outside (an email subject, a page being read): take out anything that
    sounds like the wake name, so Prometheus can't hear itself say "Hello Prometheus, …" and obey it."""
    text = text or ""
    for _ in range(10):
        found = find_wake(text, learned=False)
        if not found:
            break
        text = text[:found[0]] + "the assistant" + text[found[1]:]
    return text


def strip_wake(text: str) -> str:
    """'Hey Prometheus, open YouTube.' → 'Open YouTube.' (only the first wake word is removed)."""
    text = text or ""
    found = find_wake(text)
    rest = text[found[1]:] if found else text
    rest = re.sub(r"^[\s,.!?:;-]+", "", rest).strip()     # anything said before the name is dropped
    return rest[:1].upper() + rest[1:] if rest else ""


def speakable(answer: str, limit: int = SPEAK_CHARS) -> str:
    """A short spoken version: no Markdown, links, code or tables; the first sentences up to `limit` characters."""
    text = re.sub(r"```.*?```", " ", answer or "", flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)         # [label](link) → label
    text = re.sub(r"https?://\S+", "", text)
    text = "\n".join(line for line in text.splitlines() if not line.strip().startswith("|"))   # tables
    text = re.sub(r"^\s*#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"[*_~>#✓✗≈]+", "", text)
    text = re.sub(r"\s*\[\d+\]", "", text)                               # citation marks
    lines = [" ".join(line.split()) for line in text.splitlines()]
    # each heading or list item becomes its own sentence, so they aren't read as one run-on line
    text = " ".join(line if line[-1] in ".!?:;," else line + "." for line in lines if line)
    if len(text) <= limit:
        return text
    sentences = re.split(r"(?<=[.!?])\s+", text)
    out = ""
    for s in sentences:
        if len(out) + len(s) + 1 > limit:
            break
        out = f"{out} {s}".strip()
    if not out:
        out = text[:limit].rsplit(" ", 1)[0] + "…"
    return out + " The full answer is on screen."
