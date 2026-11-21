"""Words instead of raw values on screen (owner, 20 Nov 2026: "remove any raw unpolished data").

The raw value stays available on hover (title), so nothing is hidden from someone who wants it."""
from __future__ import annotations

import re

HIDDEN = {"", "approval", "front door", "fake"}
STATUS = {"done": "Done", "failed": "Didn't work", "waiting_approval": "Waiting for your yes", "running": "Running",
          "paused": "Paused", "stopped": "Stopped", "new": "Starting", "instant": "Done", "skipped": "Skipped"}


def _model_name(model: str) -> str:
    """'nvidia/nemotron-3-ultra-550b-a55b:free' -> 'Nemotron 3 Ultra 550B A55B (free)'."""
    name = model.rsplit("/", 1)[-1]
    name, _, tag = name.partition(":")
    name = re.sub(r"(?<=\d)-(?=\d)", ".", name)                 # claude-sonnet-4-5 -> claude-sonnet-4.5
    words = []
    for w in re.split(r"[-_ ]+", name):
        if not w:
            continue
        if re.fullmatch(r"\d+(\.\d+)?[bkm]", w, re.I) or re.fullmatch(r"[a-z]\d+[a-z]?", w, re.I):
            words.append(w.upper())                             # 550b -> 550B, a55b -> A55B
        else:
            words.append(w[:1].upper() + w[1:])
    out = " ".join(words)
    if tag == "free":
        out += " (free)"
    elif tag:
        out += " " + tag.upper()
    return out


def who_answered(raw: str) -> str:
    """The line under an answer: which AI wrote it, in words ('' = nothing to show)."""
    raw = (raw or "").strip()
    if raw.lower() in HIDDEN:
        return ""
    if raw == "instant":
        return "Answered instantly"
    if " · " not in raw:
        return raw[:1].upper() + raw[1:]
    provider, model = raw.split(" · ", 1)
    name = _model_name(model)
    if provider.strip().lower() == "ollama":
        name += " (on this laptop)"
    return "Answered by " + name


def step_status(status: str) -> str:
    return STATUS.get((status or "").strip(), (status or "").replace("_", " ").capitalize())


def step_inputs(inputs) -> list[tuple[str, str]]:
    """A step's inputs as name/value rows (never raw JSON). Long values are cut; internal ones (_x) are left out."""
    if not isinstance(inputs, dict):
        return []
    rows = []
    for key, value in inputs.items():
        if str(key).startswith("_"):
            continue
        text = value if isinstance(value, str) else ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        text = " ".join(text.split())
        rows.append((str(key).replace("_", " ").capitalize(), text[:160] + ("…" if len(text) > 160 else "")))
    return rows
