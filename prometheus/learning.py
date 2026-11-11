"""Getting better with use: the owner rates answers (good / could be better, with an optional note), and each night
Prometheus turns those ratings into a short list of rules about how to answer them. The rules are added to every
answer's instructions. Each version is kept (git), so a bad lesson can be rolled back on the Memory page.

A "could be better" answer is also rewritten straight away, using the note.
"""
from __future__ import annotations

import json
import re

from prometheus.agents.base import UNTRUSTED_RULE, untrusted

MAX_RULES = 12
RULE_CHARS = 200
SECRETISH = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|ghp_|github_pat_|password|passwd|api[ _-]?key|token\b)", re.I)
RULES_SCHEMA = {"type": "object",
                "properties": {"rules": {"type": "array", "items": {"type": "string"}}, "summary": {"type": "string"}},
                "required": ["rules", "summary"], "additionalProperties": False}
HEADER = "# What the owner has taught you\n\n"


def current_rules(text: str) -> list[str]:
    return [l[2:].strip() for l in (text or "").splitlines() if l.startswith("- ")]


def learn(services) -> str:
    """Turn new ratings into rules. Returns what changed ('' when there was nothing to learn)."""
    store, prompts = services.store, services.prompts
    items = store.feedback_to_learn()
    task_lessons = store.lessons_to_learn()
    if not items and not task_lessons:
        return ""
    rules = current_rules(prompts.get("learned"))
    lines = []
    for f in reversed(items):
        verdict = "GOOD" if f["rating"] > 0 else "COULD BE BETTER"
        lines.append(f"[{verdict}] Question: {(f['question'] or '')[:300]}\nAnswer: {(f['answer'] or '')[:600]}"
                     + (f"\nOwner's note: {f['note'][:300]}" if f["note"] else ""))
    prompt = ("Current rules for answering this owner:\n" + ("\n".join(f"- {r}" for r in rules) or "(none)")
              + "\n\nNew ratings of your answers:\n" + (untrusted("\n\n".join(lines), "ratings") if lines else "(none)")
              + ("\n\nWhat happened in tasks (what failed and what worked):\n"
                 + untrusted("\n".join(f"- {t['text']}" for t in task_lessons), "task lessons") if task_lessons else "")
              + f"\n\nRewrite the rules (at most {MAX_RULES}, each one short and concrete, about HOW to answer: length, "
              "tone, format, what to include or leave out). Keep rules that still hold, merge duplicates, drop ones "
              "the new ratings contradict. A task lesson that holds generally (e.g. 'when an app isn't installed, use "
              "its website') can become a rule. Never include personal facts, passwords or keys. Reply in JSON.")
    reply = services.router.complete("plan", [{"role": "user", "content": prompt}],
                                     "You improve how an assistant answers its owner. " + UNTRUSTED_RULE,
                                     json_schema=RULES_SCHEMA, max_tokens=900)
    try:
        data = json.loads(reply.text)
    except ValueError:
        return ""
    new = []
    for r in data.get("rules") or []:
        r = " ".join(str(r).split())[:RULE_CHARS].lstrip("- ")
        if r and not SECRETISH.search(r) and r.lower() not in {x.lower() for x in new}:
            new.append(r)
    new = new[:MAX_RULES]
    store.mark_learned([f["message_id"] for f in items])
    store.mark_lessons_learned([t["id"] for t in task_lessons])
    if not new or new == rules:
        return ""
    summary = " ".join(str(data.get("summary") or "").split())[:120] or f"{len(items)} ratings"
    prompts.set("learned", HEADER + "\n".join(f"- {r}" for r in new) + "\n", f"Learned: {summary}")
    store.audit("system", "learned", {"rules": len(new), "ratings": len(items)})
    return summary


def rewrite(services, message_id: int, note: str = "") -> str:
    """Write a better version of an answer the owner marked 'could be better'. Returns the new answer."""
    store = services.store
    old = store.message(message_id)
    if old is None or old.role != "assistant":
        raise ValueError("That isn't an answer.")
    question = next((m.text for m in reversed(store.messages(old.chat_id)) if m.role == "user" and m.id < old.id), "")
    rules = current_rules(services.prompts.get("learned"))
    prompt = (f"The owner asked: {question[:3000]}\n\nYour earlier answer:\n{untrusted(old.text[:6000], 'earlier answer')}"
              + (f"\n\nWhat the owner said was wrong: {untrusted(note[:500], 'owner note')}" if note else
                 "\n\nThe owner said it could be better.")
              + ("\n\nHow this owner likes answers:\n" + "\n".join(f"- {r}" for r in rules) if rules else "")
              + "\n\nWrite a better answer. Only the answer, no apology or preamble.")
    style = services.prompts.get("style")
    purpose = "private" if old.private else "combine"     # answers built from personal files stay local
    reply = services.router.complete(purpose, [{"role": "user", "content": prompt}],
                                     f"Writing rules:\n{style}\n\n{UNTRUSTED_RULE}", max_tokens=3000)
    text = reply.text.strip() or old.text
    store.add_message(old.chat_id, "assistant", text, f"rewritten · {reply.provider} · {reply.model}", old.private)
    return text
