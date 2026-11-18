"""The front door (M3.1): how to handle a message before any work starts.

Four ways:
- simple: talk, explanations, general knowledge. Answered straight away by a quick AI, with no plan (and streamed).
- medium: one or two actions (open, find, remind, search...). The normal plan: the planner picks the agents.
- complex: a long job with several parts. The think → act → look loop (M3.3), up to 12 rounds.
- ask: something is missing and guessing would change what Prometheus does. It asks one short question.

Plain rules decide first (instant, no AI). Only when the rules aren't sure does a free, fast AI judge, within a few
seconds; if it is slow or fails, the rules' default (medium: the normal plan) is used. Rules never judge nuanced cases
on their own: an unclear message goes to the AI, not to a guess.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

OWNER_DATA = re.compile(
    r"\b(spend(?:ing)?|spent|buy|bought|pay|paid|costs?|budgets?|money|expenses?|study|studied|hours?|"
    r"revision|data (?:is )?left|my plan|balances?|bills?|salary|marks?|grades?|attendance|exams?|deadlines?|due)\b", re.I)
OWNER = re.compile(r"\b(my|me|i)\b", re.I)
QUANTITY = re.compile(r"\b(how (?:much|many|long|often)|number|amount|percentage|total|count)\b", re.I)
ROUTES = ("simple", "medium", "complex", "ask")
AI_SECONDS = 4.0

GREETING = re.compile(r"^\s*(hi+|hey+|hello+|yo|namaste|good (morning|afternoon|evening|night)|thanks?( you)?|thank u|"
                      r"ok(ay)?|cool|nice|great|bye|good job|well done)\b[\s!.?]*$", re.I)
QUESTION = re.compile(r"^\s*(what|who|why|how|when|where|which|explain|define|describe|tell me (about|why|how|what)|"
                      r"can you explain|what'?s|whats|is it true|difference between|meaning of)\b", re.I)
# words that need a tool, the owner's own things, or fresh facts: never "simple"
ACTION = re.compile(
    r"\b(open|close|launch|start|find|search|look up|google|download|upload|install|uninstall|delete|remove|"
    r"recycle|move|copy|rename|create|make|save|write (a|an|the|me) (file|doc|pdf|note|email)|send|email|mail|"
    r"message|whatsapp|sms|call|remind|reminder|alarm|timer|schedule|book|play|pause|stop|turn (on|off)|switch|"
    r"set|change|update|sort|organi[sz]e|tidy|clean|scan|translate|summari[sz]e (this|my|the file)|read (my|this|"
    r"the) |screenshot|screen|phone|laptop|wifi|bluetooth|volume|brightness|battery|calendar|meeting|weather|news|"
    r"price|stock|today|tomorrow|yesterday|latest|current|right now|this week|repo|github|run|code|script|"
    r"chart|spreadsheet|excel|slides|pptx|docx|pdf|folder|file|files|download)s?\b", re.I)
# fresh facts or the owner's own machine and accounts: these need a tool, so never a direct answer (M3 review)
FRESH = re.compile(
    r"\b(any|new|inbox|unread|downloads|desktop|documents|disk|storage|space left|ram|memory|cpu|apps?|"
    r"process(es)?|score|scores|match|game|won|win|winner|result|results|headlines?|rain|forecast|temperature|"
    r"worth|rate|rates|bitcoin|crypto|sensex|nifty|trending|tonight|last night|now|recent|recently|this year|"
    r"election|live|status|traffic|flight|train)\b", re.I)
PERSONAL = re.compile(r"\b(my|mine|me|i am|i'm|i have|i've|our|we)\b", re.I)
LINK = re.compile(r"https?://|www\.|\b[\w-]+\.(com|in|org|net|io|dev|app)\b", re.I)
CHAIN = re.compile(r"\b(and then|then|after that|afterwards|step by step|first\b.*\bthen|finally|compare|"
                   r"research .* and|plan (my|a|the)|for each|every one of)\b", re.I)


@dataclass(frozen=True)
class Decision:
    route: str
    how: str             # "rules", "ai" or "default"
    question: str = ""   # for "ask": the one question to ask
    why: str = ""


def by_rules(text: str) -> Decision | None:
    """Clear cases only; None when the rules aren't sure."""
    t = (text or "").strip()
    if not t:
        return Decision("simple", "rules", why="empty")
    if "<untrusted" in t:
        return Decision("medium", "rules", why="has outside text")
    actions = len(ACTION.findall(t))
    if actions >= 3 and (CHAIN.search(t) or len(t) > 200):
        return Decision("complex", "rules", why=f"{actions} actions in a row")
    if OWNER_DATA.search(t) or (OWNER.search(t) and QUANTITY.search(t)):
        return Decision("medium", "rules", why="needs the owner's data")
    if GREETING.match(t):
        return Decision("simple", "rules", why="a greeting")
    if actions == 0 and not FRESH.search(t) and not LINK.search(t) and not PERSONAL.search(t) and len(t) <= 300 \
            and QUESTION.match(t):
        return Decision("simple", "rules", why="a general question")
    if 1 <= actions <= 2 and not CHAIN.search(t) and len(t) <= 300:
        return Decision("medium", "rules", why="one or two actions")
    return None


CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {"route": {"type": "string", "enum": list(ROUTES)}, "question": {"type": "string"}},
    "required": ["route", "question"],
    "additionalProperties": False,
}
CLASSIFY_SYSTEM = (
    "You sort one message for a personal assistant that runs on its owner's laptop. Reply with JSON only.\n"
    "route: \"simple\" = talk, explanations or general knowledge the assistant can answer at once with no tools; "
    "\"medium\" = it needs one or two actions (files, apps, the web, reminders, email, the phone); "
    "\"complex\" = a long job with several parts that depend on each other; "
    "\"ask\" = something needed is missing AND guessing would change what the assistant does (for example two "
    "people it could mean, or no file named). Do not ask when a sensible default exists.\n"
    "question: for \"ask\", the one short question to ask the owner; otherwise \"\".")


def by_ai(router, text: str, history: list[dict] | None = None, seconds: float = AI_SECONDS) -> Decision | None:
    """A free, fast AI judges the unclear case, within `seconds`. None if it is slow, fails or would need paid AI."""
    box: dict = {}

    def ask():
        try:
            msgs = list(history or [])[-4:] + [{"role": "user", "content": f"Message: {text[:2000]}"}]
            # "research" is never the hand-picked lead AI; free_only: a classifier never costs money (M3 review)
            kind = router.step_kind("summarise") if hasattr(router, "step_kind") else _null()
            cheap = router.free_only() if hasattr(router, "free_only") else _null()
            with kind, cheap:
                reply = router.complete("research", msgs, CLASSIFY_SYSTEM, json_schema=CLASSIFY_SCHEMA, max_tokens=200)
            box["reply"] = reply.text
        except Exception as e:           # no free AI, a paid one would be needed, the kill switch: rules decide
            box["error"] = e

    import contextvars

    worker = threading.Thread(target=contextvars.copy_context().run, args=(ask,), name="prometheus-front-door",
                              daemon=True)
    worker.start()
    worker.join(seconds)
    raw = box.get("reply")
    if not raw:
        return None
    try:
        from prometheus.orchestrator.planner import json_part

        data = json.loads(json_part(raw))
        route = str(data.get("route", "")).lower()
        question = " ".join(str(data.get("question") or "").split())[:300]
    except (ValueError, AttributeError):
        return None
    if route not in ROUTES or (route == "ask" and not question):
        return None
    return Decision(route, "ai", question=question if route == "ask" else "")


def decide(router, text: str, history: list[dict] | None = None) -> Decision:
    return by_rules(text) or by_ai(router, text, history) or Decision("medium", "default", why="unsure: normal plan")


class _null:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False
