"""Practice tests: real requests the owner makes, re-checked after every update. (From the "Build your first AI agent"
guide: "20 to 30 real cases, pass or fail, before you trust it".)

Each test is something the owner said and what should handle it: "instant" (answered without AI, like the time),
"answer" (a plain reply), or an agent name (like "email"). A check only plans, it never does anything: it asks the
planner how it would handle the request and compares. So a broken update (the weather turning into a GitHub
search) shows up as a failed test, without any action being taken.
"""
from __future__ import annotations

import json
import time

KV_TESTS, KV_LAST = "practice_tests", "practice_last"
MAX_TESTS = 40
# a starting set, from what went wrong before; the owner adds their own
STARTERS = [
    {"said": "what time is it", "expect": "instant"},
    {"said": "what's the date today", "expect": "instant"},
    {"said": "what's the weather tomorrow", "expect": "instant"},
    {"said": "open youtube", "expect": "instant"},
    {"said": "set a timer for ten minutes", "expect": "instant"},
    {"said": "remind me to call mum at 6pm", "expect": "instant"},
    {"said": "quiz me on my flashcards", "expect": "instant"},
    {"said": "how much did I spend this month", "expect": "instant"},
    {"said": "what assignments are due this week", "expect": "instant"},
    {"said": "what is photosynthesis", "expect": "answer"},
]


def tests(store) -> list[dict]:
    raw = store.kv_get(KV_TESTS)
    if raw is None:
        return [dict(t) for t in STARTERS]
    try:
        return [t for t in json.loads(raw) if isinstance(t, dict) and t.get("said") and t.get("expect")]
    except (TypeError, ValueError):
        return [dict(t) for t in STARTERS]


def save(store, items: list[dict]) -> None:
    store.kv_set(KV_TESTS, json.dumps(items[-MAX_TESTS:]))


def add(store, said: str, expect: str) -> str:
    said = " ".join((said or "").split())[:200]
    if not said:
        return "There's nothing to save as a test."
    items = [t for t in tests(store) if t["said"].lower() != said.lower()]
    items.append({"said": said, "expect": expect})
    save(store, items)
    return f"Saved as a practice test: “{said}” should go to {expect}. ({len(items)} tests.)"


def from_last_task(store, chat_id: int) -> str:
    """'Save this as a practice test': the last request in this chat, with how it was handled when it worked."""
    rows = store._rows("SELECT id, text, status FROM tasks WHERE chat_id = ? AND text NOT LIKE '%practice test%' "
                       "ORDER BY id DESC LIMIT 1", (chat_id,))
    if not rows:
        return "There's no earlier request in this chat to save."
    task = rows[0]
    steps = store._rows("SELECT agent FROM steps WHERE task_id = ? ORDER BY id", (task["id"],))
    expect = steps[0]["agent"] if steps else "answer"
    from prometheus.quick import understand

    if understand(task["text"], store) is not None:
        expect = "instant"
    return add(store, task["text"], expect)


def how_handled(orchestrator, said: str) -> str:
    """'instant', 'answer' or the first agent the planner would use. Plans only; nothing is run."""
    from prometheus.quick import understand

    if understand(said, orchestrator.store) is not None:
        return "instant"
    plan = orchestrator._plan(said)
    if plan.steps:
        return str(plan.steps[0].get("agent", "?"))
    return "answer"


def run(orchestrator, items: list[dict] | None = None) -> dict:
    items = tests(orchestrator.store) if items is None else items
    passed, failed = 0, []
    for t in items:
        try:
            got = how_handled(orchestrator, t["said"])
        except Exception as e:
            got = f"error ({type(e).__name__})"
        if got == t["expect"]:
            passed += 1
        else:
            failed.append({"said": t["said"], "expect": t["expect"], "got": got})
    result = {"when": time.time(), "passed": passed, "total": len(items), "failed": failed}
    orchestrator.store.kv_set(KV_LAST, json.dumps(result))
    return result


def describe(result: dict) -> str:
    text = f"{result['passed']} of {result['total']} passed."
    if result["failed"]:
        text += " Failed: " + "; ".join(f"“{f['said']}” went to {f['got']}, not {f['expect']}"
                                        for f in result["failed"][:5])
    return text


def last_result(store) -> str:
    raw = store.kv_get(KV_LAST)
    if not raw:
        return ""
    try:
        r = json.loads(raw)
    except (TypeError, ValueError):
        return ""
    return describe(r) + f" (checked {time.strftime('%d %b %H:%M', time.localtime(r['when']))})"
