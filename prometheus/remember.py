"""Memory by command, without any AI: "remember…", "update memory…", "forget…", "change X to Y in memory",
"what do you remember about…".

The owner asked that telling Prometheus to remember something ALWAYS saves it. So these phrases are handled here,
before any AI is asked: the words are saved exactly as said and the answer confirms what was saved.
"""
from __future__ import annotations

import re

SAVE = re.compile(
    r"^\W*(?:please\s+|ok(?:ay)?\s+|hey\s+)?(?:in (?:the|your) memory,?\s+)?"
    r"(?:remember|note down|make a note|note|don'?t forget|keep in mind|memori[sz]e|"
    r"(?:update|add to|save (?:to|in)|put (?:in|into)|store in)\s+(?:your\s+|the\s+|my\s+)?memor(?:y|ies))"
    r"(?:\s+(?:that|this|it))?\s*[:,\-]?\s+(?P<what>.+)$", re.I | re.S)
NOT_SAVE = re.compile(r"^(?:when|what|how|who|why|where|if|whether|me\b|the time|last)\b", re.I)   # "remember when we…?"
REMINDER = re.compile(r"^to\s+.+\b(?:at \d|in \d+ (?:min|hour|day)|tomorrow|tonight|every (?:day|week|morning|night)|"
                      r"on (?:mon|tue|wed|thu|fri|sat|sun))", re.I)                                 # that's a reminder
SECOND = re.compile(r"\s+(?:and|also|then)\s+(?=(?:can|could|will|would)\s+(?:u|you)\b|please\b|tell me\b|what\b|how\b)",
                    re.I)
FORGET = re.compile(r"^\W*(?:please\s+)?(?:forget|delete|remove|erase)\s+(?:that\s+|about\s+|what i said about\s+)?"
                    r"(?P<what>.+?)(?:\s+from (?:your |the |my )?memor(?:y|ies))?\W*$", re.I | re.S)
FORGET_NEEDS_MEMORY = re.compile(r"^\W*(?:please\s+)?(?:delete|remove|erase)\b", re.I)   # these need "from memory"
CHANGE = re.compile(r"^\W*(?:please\s+)?(?:change|update|correct|replace)\s+(?P<old>.+?)\s+(?:to|with)\s+(?P<new>.+?)"
                    r"\s+in (?:your |the |my )?memor(?:y|ies)\W*$", re.I | re.S)
SHOW = re.compile(r"^\W*(?:please\s+)?(?:what do you (?:remember|know)(?: about (?:me|(?P<about>.+?)))?|"
                  r"(?:show|list)(?: me)? (?:your |my |the )?memor(?:y|ies)(?: about (?P<about2>.+?))?)\W*$", re.I)
SECRET = re.compile(r"(?i)\b(password|passcode|pin|otp|cvv|api[ _-]?key|secret|token)\b.{0,20}[:=]?\s*\S{4,}|"
                    r"\b(?:sk|ghp|gho|xox[bp])[-_][A-Za-z0-9_\-]{12,}|\b\d{13,19}\b")
SMALL = {"the", "a", "an", "my", "is", "are", "was", "that", "this", "of", "to", "in", "on", "and", "it", "i", "me"}
MAX_LEN = 400
SHOWN = 10


def _clean(text: str) -> str:
    return " ".join((text or "").split()).strip(" .!")


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[^\W_]+", text.lower()) if w not in SMALL]


def _matching(store, phrase: str) -> list:
    words = _words(phrase)
    if not words:
        return []
    return [m for m in store.memories() if all(w in m.text.lower() for w in words)]


def handle(store, chat_id: int, text: str) -> tuple[str, str] | None:
    """(what to say, the rest of the message still to do) or None when the message isn't a memory command."""
    t = (text or "").strip()
    if not t or len(t) > 1500:
        return None
    m = CHANGE.match(t)
    if m:
        old, new = _clean(m.group("old")), _clean(m.group("new"))
        hits = _matching(store, old)
        if not hits:
            return f"I don't have anything about “{old}” in memory. Say “remember …” to add it.", ""
        if SECRET.search(new):
            return "That looks like a password or a key. I don't keep those in memory.", ""
        changed = []
        for hit in hits[:5]:
            updated = re.sub(re.escape(old), new, hit.text, flags=re.I)
            if updated == hit.text:
                updated = new
            store.delete_memory(hit.id)
            store.add_memory(updated[:MAX_LEN], source=hit.source or "chat")
            changed.append(updated[:MAX_LEN])
        return "Updated in memory: " + "; ".join(f"“{c}”" for c in changed) + ".", ""
    m = SHOW.match(t)
    if m:
        about = _clean(m.group("about") or m.group("about2") or "")
        items = _matching(store, about) if about else store.memories()
        if not items:
            return ("I don't have anything about that in memory yet." if about else
                    "My memory is empty so far. Say “remember …” to add something."), ""
        newest = list(reversed(items))[:SHOWN]
        more = f"\n\n({len(items) - SHOWN} more on the Memory page.)" if len(items) > SHOWN else ""
        return "Here's what I remember" + (f" about {about}" if about else "") + ":\n" + "\n".join(
            f"- {i.text}" for i in newest) + more, ""
    m = SAVE.match(t)
    if m:
        what = m.group("what").strip()
        rest = ""
        parts = SECOND.split(what, maxsplit=1)
        if len(parts) == 2:                       # "remember X and can you also …?": save X, then do the rest
            what, rest = parts[0], parts[1].strip()
        if NOT_SAVE.match(what) or what.rstrip().endswith("?") or REMINDER.match(what):
            return None
        what = _clean(re.sub(r"^(?:that|to)\s+", lambda mm: "" if mm.group(0).lower().startswith("that") else mm.group(0),
                             what, flags=re.I))
        what = _clean(re.sub(r"^(?:please\s+)|(?:[\s,]+please)$", "", what, flags=re.I))
        # "remember this please", "remember things": nothing definite to save, so the AI works out what is meant
        if len(_words(what)) < 2 and not re.search(r"\d", what):
            return None
        if SECRET.search(what):
            return "That looks like a password or a key. I don't keep those in memory.", rest
        what = what[0].upper() + what[1:]
        what = what[:MAX_LEN]
        if any(x.text.lower() == what.lower() for x in store.memories()):
            return f"I already have that in memory: “{what}”.", rest
        title = dict(store.chats()).get(chat_id, "chat")
        store.add_memory(what, source=f"chat: {title}")
        return f"Saved to memory: “{what}”. (Say “forget {what[:40]}” to remove it.)", rest
    m = FORGET.match(t)
    if m:
        what = _clean(m.group("what"))
        from_memory = bool(re.search(r"from (?:your |the |my )?memor(?:y|ies)\W*$", t, re.I))
        if what.lower() in ("it", "that", "this", "everything", "all") or (FORGET_NEEDS_MEMORY.match(t) and not from_memory):
            return None
        hits = _matching(store, what)
        if not hits:
            return None if not from_memory else (f"I don't have anything about “{what}” in memory.", "")
        if len(hits) > 5:
            return (f"{len(hits)} memories mention “{what}”. Say more exactly which one, or remove them on the "
                    "Memory page."), ""
        for hit in hits:
            store.delete_memory(hit.id)
        return "Forgotten: " + "; ".join(f"“{h.text}”" for h in hits) + ".", ""
    return None
