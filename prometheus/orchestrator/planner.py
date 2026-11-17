"""Lead AI (HuggingGPT pattern): plan -> pick agent -> run steps -> combine one answer."""
from __future__ import annotations

import contextlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from prometheus.agents.base import UNTRUSTED_RULE, AgentContext, StepResult, untrusted
from prometheus.models.base import ProviderUnavailable
from prometheus.safety.budget import BudgetExceeded
from prometheus.safety.killswitch import Stopped

MAX_STEPS = 8
JOB_KINDS = ("chat", "plan", "research", "code", "vision", "summarise")   # what a step may be tagged (M3.4)
PACKAGE_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
REF = re.compile(r"^\$step(\d+)\.(\w+)$")

HISTORY_TURNS = 12
PASSAGES = 3
PASSAGE_CHARS = 1000
CHAT_PASSAGES = 3          # parts of earlier chats fetched per request (never whole chats)
CHAT_PASSAGE_CHARS = 1200
KNOWN_TOPICS = 40
NAME_UNTIL = 2             # the planner may improve a chat's name during its first two requests
HISTORY_CHARS = 1500
# chat messages that carry outside text (file names from watched folders, a project file someone sent you): replayed to
# the model marked as outside text, never as the owner's own words (M2.4 review)
OUTSIDE_MESSAGES = {"imported": "a chat imported from a project file", "automation": "an automation (names of new files)",
                    "watcher": "a watcher (names of new files)"}
SUMMARY_EVERY = 10
MAX_REMEMBER = 3
MAX_FACTS = 6
ASKED_TO_REMEMBER = re.compile(r"\b(remember|don'?t forget|do not forget|keep in mind|note that|save this|from now on)\b", re.I)
SECRETISH = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|ghp_|github_pat_|xox[bp]-|AKIA[0-9A-Z]{8,}|pass(word|phrase)|passwd|secret|"
                       r"api[ _-]?key|token\b|\bpin\b|\b[A-Za-z0-9+/_-]{32,}\b)", re.I)
PRIVATE_PLACEHOLDER = "(an answer with details from your files, written on your laptop and not shown here)"

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "topic": {"type": "string"}, "title": {"type": "string"},
                   "remember": {"type": "array", "items": {"type": "string"}},
                   "facts": {"type": "array", "items": {
                       "type": "object",
                       "properties": {"subject": {"type": "string"}, "attribute": {"type": "string"},
                                      "value": {"type": "string"}},
                       "required": ["subject", "attribute", "value"],
                       "additionalProperties": False}},
                   "steps": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "agent": {"type": "string"},
            "goal": {"type": "string"},
            "skill": {"type": "string"},
            "inputs": {"type": "array", "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
                "additionalProperties": False,
            }},
            "depends_on": {"type": "array", "items": {"type": "integer"}},
            "kind": {"type": "string", "enum": ["chat", "plan", "research", "code", "vision", "summarise", ""]},
        },
        "required": ["id", "agent", "goal", "skill", "inputs", "depends_on", "kind"],
        "additionalProperties": False,
    }}},
    "required": ["answer", "topic", "title", "remember", "facts", "steps"],
    "additionalProperties": False,
}


REPAIR_SCHEMA = {
    "type": "object",
    "properties": {"action": {"type": "string", "enum": ["retry", "give_up"]}, "agent": {"type": "string"},
                   "goal": {"type": "string"}, "why": {"type": "string"},
                   "inputs": {"type": "array", "items": {"type": "object", "properties": {
                       "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"],
                       "additionalProperties": False}}},
    "required": ["action", "agent", "goal", "why", "inputs"],
    "additionalProperties": False,
}
STEP_TRIES = 3            # tries per step, counting the first one (owner's choice)
TASK_BUDGET = 0.20        # USD of paid AI one task may use (owner's choice)
MAX_TASK_STEPS = 12       # steps one task may run, counting retries


# What a read-only request (another AI app's "ask") may do: these agents, only these actions (None = all).
READ_ONLY_STEPS = {
    "files": {"find", "search_text", "list", "info"}, "email": {"unread", "search", "read"}, "calendar": None,
    "google": {"events", "drive_search", "drive_read"}, "documents": None, "research": None, "wikipedia": None,
    "clipboard": None, "photos": {"find", "status"}, "study": {"decks"}, "money": {"report", "today"},
    "exams": {"show", "today"}, "assignments": {"list"}, "obsidian": {"search", "read", "recent"},
    "reminders": {"list"}, "focus": {"status"}, "repo_scout": None, "guide": None,
    "system": {"wifi_status", "wifi_list", "bluetooth_status", "battery", "apps_running"},
}


def read_only_ok(step: dict) -> bool:
    allowed = READ_ONLY_STEPS.get(step.get("agent"), "no")
    if allowed == "no":
        return False
    return allowed is None or str(step.get("inputs", {}).get("action", "")).strip().lower() in allowed


class PlanError(Exception):
    pass


class Paused(Exception):
    """The owner pressed Pause: the task stops and its remaining steps are kept for Carry on."""


# ---- pausing a task the owner started: where each paused task stopped, so it can carry on ------------------
PAUSED_KV = "paused_runs"
PAUSED_MAX = 20                  # the newest few are kept; older ones just can't carry on


def paused_runs(store) -> dict:
    try:
        kept = store.kv_get(PAUSED_KV, {})
    except Exception:
        return {}
    return kept if isinstance(kept, dict) else {}


def paused_run(store, task_id: int) -> dict | None:
    """What a paused task has left to do (None if it can't carry on, e.g. it was stopped by an AI limit)."""
    found = paused_runs(store).get(str(int(task_id)))
    return found if isinstance(found, dict) else None


def save_paused_run(store, task_id: int, data: dict) -> None:
    kept = paused_runs(store)
    kept[str(int(task_id))] = data
    for old in sorted(kept, key=lambda k: int(k))[:-PAUSED_MAX]:
        kept.pop(old, None)
    store.kv_set(PAUSED_KV, kept)


def drop_paused_run(store, task_id: int) -> None:
    kept = paused_runs(store)
    if kept.pop(str(int(task_id)), None) is not None:
        store.kv_set(PAUSED_KV, kept)



@dataclass
class Plan:
    steps: list[dict]
    answer: str = ""                                   # a direct reply (no tools needed)
    remember: list[str] = field(default_factory=list)  # facts the user asked to remember
    facts: list[dict] = field(default_factory=list)     # (subject, attribute, value) facts from this message
    model: str = ""                                    # which model wrote the plan or answer
    topic: str = ""                                    # what the request is about, e.g. "weather"
    title: str = ""                                    # a short name for the chat


@dataclass
class TaskOutcome:
    task_id: int
    status: str
    answer: str


def _value(v):
    if isinstance(v, str) and v.strip()[:1] in ("[", "{"):
        try:
            return json.loads(v)
        except ValueError:
            return v
    return v


def _inputs(raw) -> dict:
    if isinstance(raw, dict):
        return {str(k): _value(v) for k, v in raw.items()}
    if isinstance(raw, list):
        out = {}
        for pair in raw:
            if not isinstance(pair, dict) or "key" not in pair:
                raise PlanError("Each input needs a key and a value.")
            out[str(pair["key"])] = _value(pair.get("value", ""))
        return out
    raise PlanError("Inputs must be key/value pairs.")


PAID_YES = re.compile(r"^\W*(?:ok(?:ay)?[, ]+)?(?:yes|yeah|yep|sure|go ahead)?[, ]*(?:you can )?use (?:the )?paid"
                      r"(?: ai| one| model)?(?: (?:for (?:this|it|that)|this once|once|this time))?\W*$", re.I)
CODE_REQUEST = re.compile(r"\b(code|coding|script|python|javascript|typescript|java|c\+\+|rust|html|css|function|"
                          r"bug|debug|compile|program(?:me)?|algorithm|regex|sql|refactor|unit tests?|stack ?trace|"
                          r"traceback|pull request|commit|repo(?:sitory)?)\b", re.I)
# "@research what's new in solar panels": the owner picks the agent for this message (M2.2). Enforced in code: the
# planner only sees that agent and a plan naming any other one is refused.
MENTION = re.compile(r"^\s*@([A-Za-z_]{2,30})\b[\s,:]*")


def json_part(raw: str) -> str:
    """The JSON object in an AI's reply, without its "thinking", a sentence before or after, or a code fence."""
    text = re.sub(r"<think(?:ing)?>.*?</think(?:ing)?>", "", raw or "", flags=re.S | re.I).strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        return fence.group(1)
    start = text.find("{")
    if start < 0:
        return text
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            esc, in_str = (not esc and c == "\\"), (in_str if (esc or c != '"') else False)
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return text[start:]


def validate_plan(raw: str, registry: dict) -> Plan:
    text = json_part(raw)
    try:
        data = json.loads(text)
        steps = data.get("steps") or []
        answer = str(data.get("answer") or "").strip()
        remember = [str(r) for r in (data.get("remember") or []) if isinstance(r, (str, int, float))]
        topic = str(data.get("topic") or "")
        title = str(data.get("title") or "")
        facts = [{k: str(f.get(k) or "") for k in ("subject", "attribute", "value")}
                 for f in (data.get("facts") or []) if isinstance(f, dict)][:MAX_FACTS]
    except (ValueError, AttributeError, TypeError) as e:
        raise PlanError(f"The plan is not valid JSON with a 'steps' list ({e}).") from e
    if not isinstance(steps, list) or len(steps) > MAX_STEPS:
        raise PlanError(f"The plan must have at most {MAX_STEPS} steps.")
    if not steps and not answer:
        raise PlanError("The reply has no answer and no steps.")
    seen: set[int] = set()
    clean = []
    for s in steps:
        if not isinstance(s, dict):
            raise PlanError("Each step must be an object.")
        sid = s.get("id")
        if not isinstance(sid, int) or sid in seen:
            raise PlanError(f"Step ids must be unique numbers (problem with {sid!r}).")
        if s.get("agent") not in registry:
            raise PlanError(f"Unknown agent: {s.get('agent')!r}. Use one of: {', '.join(registry)}.")
        deps = s.get("depends_on") or []
        if not all(isinstance(d, int) and d in seen for d in deps):
            raise PlanError(f"Step {sid} may depend only on earlier steps.")
        inputs = _inputs(s.get("inputs", {}))
        for key in getattr(registry[s["agent"]], "required_inputs", ()):
            value = inputs.get(key)
            if value is None or (isinstance(value, str) and not value.strip()) or value == []:
                raise PlanError(f"Step {sid} ({s['agent']}) needs the input '{key}'. Give it a real value, "
                                "for example a link for the opener or an app name for the system agent.")
        kind = str(s.get("kind") or "").strip().lower()
        clean.append({"id": sid, "agent": s["agent"], "goal": str(s.get("goal", "")),
                      "kind": kind if kind in JOB_KINDS else "",
                      "skill": str(s.get("skill") or "").strip(),
                      "inputs": inputs, "depends_on": list(deps)})
        seen.add(sid)
    return Plan(clean, answer=answer, remember=remember, topic=topic, title=title, facts=facts)


def step_is_private(agent_cls, inputs) -> bool:
    """Does this step's result hold personal data? Some agents always do; a script does when it is given your files."""
    if getattr(agent_cls, "private_outputs", False):
        return True
    if not getattr(agent_cls, "private_with_files", False):
        return False
    files = inputs.get("files") if isinstance(inputs, dict) else None
    if files is None and isinstance(inputs, list):                      # the plan's [{"key", "value"}] form
        files = next((i.get("value") for i in inputs if isinstance(i, dict) and i.get("key") == "files"), None)
    return bool(files)


def resolve_inputs(inputs: dict, results: dict[int, StepResult], private_steps: frozenset[int] = frozenset(),
                   allow_private: bool = True) -> dict:
    out = {}
    for key, value in inputs.items():
        m = REF.match(value) if isinstance(value, str) else None
        if m:
            sid, field = int(m.group(1)), m.group(2)
            if sid in private_steps and not allow_private:
                raise PlanError(f"Step {sid} holds private data from your files; it can't be sent to the web or cloud.")
            if sid in private_steps:
                out["_from_private"] = True          # an agent that could use the cloud must not, for this text
            if sid in results and field == "summary" and field not in results[sid].data:
                out[key] = results[sid].summary          # the text a step wrote, e.g. to save it in a file
                continue
            if sid not in results or field not in results[sid].data:
                raise PlanError(f"Step result {value} does not exist.")
            out[key] = results[sid].data[field]
        else:
            out[key] = value
    return out


class Orchestrator:
    def __init__(self, store, router, registry: dict, make_context: Callable[[int, int], AgentContext],
                 killswitch, prompts_dir: Path | None = None, skills=None):
        self.store = store
        self.router = router
        self.registry = registry
        self.make_context = make_context
        self.killswitch = killswitch
        self.prompts_dir = Path(prompts_dir) if prompts_dir else PACKAGE_PROMPTS
        self.step_tries, self.task_budget, self.max_steps = STEP_TRIES, TASK_BUDGET, MAX_TASK_STEPS
        self.extra_catalog = None          # callable: tools of connected apps, for the planner
        self.extra_sections: list = []     # (heading, callable returning text) added to the planner's system
        self.interceptors: list = []       # callables (chat_id, text) -> answer or None: e.g. a quiz in progress
        self.before_steps = None           # callable (chat_id, text, plan) -> answer to stop with, or "" to go on
        self.stopping: set[int] = set()    # chats whose reply the owner stopped (Stop button): ends before the next step
        self.project_context = None        # callable (chat_id) -> text about the chat's project (M2.3), or None
        self.front_door = False            # M3.1: rules first, a free fast AI when unsure (on in the real app)
        self.loop = None                   # M3.3: the think-act-look loop for complex jobs
        self.live_text: dict[int, str] = {}  # M3.5: an answer being written, per chat
        self.live_context = None           # M3.6: callable -> "Right now: ..." (counts only, no titles)
        self.pausing: set[int] = set()     # chats the owner paused: ends before the next step, the rest is kept
        self.skills = skills
        from prometheus.memory.facts import Facts

        self.facts = Facts(store)
        self._remembered: dict[int, list[str]] = {}

    def _style(self) -> str:
        """The writing rules plus what the owner has taught Prometheus by rating answers."""
        style = self._prompt("style")
        try:
            learned = [l for l in self._prompt("learned").splitlines() if l.startswith("- ")]
        except OSError:
            learned = []
        if learned:
            style += "\n\nWhat this owner has taught you (follow these, they win over the rules above):\n" + "\n".join(learned)
        return style

    def _prompt(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        if not path.exists():
            path = PACKAGE_PROMPTS / f"{name}.md"
        return path.read_text(encoding="utf-8")

    def _summarise_older(self, chat_id: int, older: list) -> str:
        """A short running summary of the turns that no longer fit. Written by a cheap model, rewritten only every
        SUMMARY_EVERY new old turns. Private answers are never shown to it."""
        text, upto = self.store.get_summary(chat_id)
        fresh = [m for m in older if m.id > upto and m.role in ("user", "assistant")]
        if len(fresh) < SUMMARY_EVERY:
            return text
        lines = "\n".join(f"{m.role}: {(PRIVATE_PLACEHOLDER if m.private else m.text)[:600]}" for m in fresh)
        prompt = (f"Earlier summary:\n{text or '(none)'}\n\nNewer turns:\n{untrusted(lines, 'chat')}\n\n"
                  "Write one updated summary (under 180 words): what the person wants, decisions made, names, "
                  "numbers, preferences and anything still unfinished. Facts only.")
        try:
            reply = self.router.complete("research", [{"role": "user", "content": prompt}],
                                         "You keep a running summary of a conversation. " + UNTRUSTED_RULE, max_tokens=500)
        except (ProviderUnavailable, BudgetExceeded, Stopped):
            return text
        self.store.set_summary(chat_id, reply.text.strip()[:1500], older[-1].id)
        return reply.text.strip()[:1500]

    def _history(self, chat_id: int) -> list[dict]:
        """Recent turns of this chat for the model, plus a summary of older ones. Answers built from the owner's files
        are replaced by a placeholder."""
        messages = self.store.messages(chat_id)
        recent, older = messages[-HISTORY_TURNS:], messages[:-HISTORY_TURNS]
        turns = []
        for m in recent:
            if m.role in ("user", "assistant"):
                content = (PRIVATE_PLACEHOLDER if m.private else m.text)[:HISTORY_CHARS]
                if m.model in OUTSIDE_MESSAGES and not m.private:
                    content = untrusted(content, OUTSIDE_MESSAGES[m.model])
                turns.append({"role": m.role, "content": content})
        while turns and turns[0]["role"] != "user":
            turns.pop(0)
        summary = self._summarise_older(chat_id, older) if older else ""
        if summary:
            turns = [{"role": "user", "content": f"Summary of the earlier conversation: {summary}"},
                     {"role": "assistant", "content": "Understood."}] + turns
        return turns

    def _remember(self, chat_id: int, facts: list[str], user_text: str = "") -> None:
        """Save facts only when the owner's own message asks for it, and never anything that looks like a secret."""
        if not ASKED_TO_REMEMBER.search(user_text or ""):
            return
        facts = [f for f in facts if not SECRETISH.search(f)]
        known = {m.text.lower() for m in self.store.memories()}
        title = dict(self.store.chats()).get(chat_id, "chat")
        saved = 0
        for fact in facts:
            fact = " ".join(fact.split())[:300]
            if fact and fact.lower() not in known and saved < MAX_REMEMBER:
                self.store.add_memory(fact, source=f"chat: {title}")
                known.add(fact.lower())
                saved += 1

    def _save_facts(self, chat_id: int, task_id: int, facts: list[dict]) -> None:
        """Lasting facts from the owner's message are saved by themselves and listed under the answer."""
        from prometheus.features import memory_collects

        if not memory_collects(getattr(self, "settings", None), chat_id):   # Memory → Privacy
            return
        title = dict(self.store.chats()).get(chat_id, "chat")
        noted, saved_ids = [], []
        for f in facts[:MAX_FACTS]:
            if SECRETISH.search(f"{f['attribute']} {f['value']}"):
                continue
            fid, said = self.facts.save(f["subject"], f["attribute"], f["value"], source=f"chat: {title}")
            if fid:
                noted.append(said)
                saved_ids.append(fid)
        if noted:
            self._remembered[task_id] = noted
            from prometheus import undo

            undo.record(self.store, "fact", "remembering " + "; ".join(noted)[:200], {"ids": saved_ids})

    def _earlier_chats(self, text: str, chat_id: int | None) -> str:
        """The few parts of earlier chats that match this request. Parts already in the recent history are skipped;
        parts holding private file details are never sent (this goes to the planning model)."""
        skip = []
        if chat_id is not None:
            recent = self.store.messages(chat_id)[-HISTORY_TURNS:]
            first_recent = recent[0].id if recent else 0
            skip = [s["doc_id"] for s in self.store.segments(chat_id=chat_id) if s["last_msg"] >= first_recent]
        hits = self.store.search_chunks(text, limit=CHAT_PASSAGES, include_private=False, chats=True,
                                        exclude_doc_ids=skip)
        if not hits:
            return ""
        return "From earlier chats (only the matching parts):\n" + "\n".join(
            untrusted(h.context[:CHAT_PASSAGE_CHARS], h.title) for h in hits) + "\n\n"

    def _context(self, text: str, chat_id: int | None) -> str:
        """What the AI is told besides the request: now (live context), memories, facts, earlier chats, the chat's
        project, lessons and saved passages. Shared by planning and quick answers."""
        relevant = [h.text for h in self.store.recall(text, limit=8, kinds=("memory", "chat_note"))]
        newest = [m.text for m in self.store.memories()[-3:]]
        remembered = list(dict.fromkeys(relevant + newest))
        memories = "\n".join(f"- {m[:400]}" for m in remembered) or "(none)"
        from prometheus.quick import now_line

        live = ""
        if self.live_context is not None:            # M3.6: counts only (reminders, tasks, approvals, battery)
            try:
                live = self.live_context() or ""
            except Exception:
                live = ""
        content = f"{now_line()}{' ' + live if live else ''}\n\nThings to remember about the user:\n{memories}\n\n"
        known_facts = self.facts.relevant(text)
        if known_facts:
            content += "Known facts (people, places and things in the owner's life):\n" + "\n".join(
                f"- {f}" for f in known_facts) + "\n\n"
        # Passages from web pages read before (never private files: this goes to the planning model).
        content += self._earlier_chats(text, chat_id)
        if self.project_context is not None and chat_id is not None:
            try:
                content += self.project_context(chat_id)      # the chat's project: folder, instructions, notes
            except Exception:
                pass
        lessons = self.store.relevant_lessons(text)
        if lessons:
            content += "Lessons from earlier tasks (follow them):\n" + "\n".join(f"- {l}" for l in lessons) + "\n\n"
        passages = self.store.search_chunks(text, limit=PASSAGES, include_private=False, chats=False)
        if passages:
            content += "Saved passages that may help:\n" + "\n".join(
                untrusted(h.match[:PASSAGE_CHARS], h.source or h.title) for h in passages) + "\n\n"
        return content

    def _plan(self, text: str, history: list[dict] | None = None, chat_id: int | None = None, only: str = "") -> Plan:
        usable = [(n, c) for n, c in self._usable_agents() if not only or n == only]
        registry = dict(usable) if only else self.registry
        from prometheus.orchestrator.registry import planner_line

        agents = "\n".join(planner_line(name, cls) for name, cls in usable)
        if only:
            agents += (f"\n\nThe owner chose the {only} agent for this message (they wrote @{only}). Use only that "
                       "agent, or answer directly when no step is needed.")
        system = f"{self._prompt('planner')}\n\nWriting rules:\n{self._style()}\n\nAgents:\n{agents}"
        catalog = self.skills.catalog_text() if self.skills else ""
        if catalog:
            system += ("\n\nSkills (how-to guides). For each step set \"skill\" to the name of the one guide that "
                       f"fits, or \"\" if none does:\n{catalog}")
        if self.extra_catalog is not None:
            try:
                connected = self.extra_catalog()
            except Exception:
                connected = ""
            if connected:
                system += ("\n\nConnected apps (use the apps agent: action call, app, tool, arguments as a JSON "
                           "object):\n" + connected)
        for heading, make in self.extra_sections:
            try:
                text_ = make()
            except Exception:
                text_ = ""
            if text_:
                system += f"\n\n{heading}:\n{text_}"
        known = [t["topic"] for t in self.store.topics(KNOWN_TOPICS)]
        system += ("\n\nTopic: set \"topic\" to 1 to 3 lowercase words naming what this request is about (for example "
                   "weather, taxes, python learning). Reuse one of these earlier topics when it fits: "
                   + (", ".join(known) if known else "(none yet)"))
        system += ("\n\nTitle: set \"title\" to a short, specific name for this conversation (2 to 6 words, "
                   "for example \"Delhi weather this week\" or \"Fix Python loop error\"; no quotes, no full stop). "
                   "Name what the conversation is about, not the greeting.")
        system += f"\n\n{UNTRUSTED_RULE}"
        content = self._context(text, chat_id)
        messages = list(history or []) + [{"role": "user", "content": content + f"Request: {text}"}]
        # coding requests go to the coding brain (Ripple) when one is set up; everything else to the normal one
        purpose = "code" if CODE_REQUEST.search(text or "") and self.router.routes.get("code") else "plan"
        # Each AI gets one chance to correct an unusable answer; then the next AI in line tries (up to three).
        tried: list[str] = []
        skip: frozenset[str] = frozenset()
        plan = None
        for _ in range(3):
            try:
                reply = self.router.complete(purpose, messages, system, json_schema=PLAN_SCHEMA, max_tokens=4000,
                                             skip=skip)
            except ProviderUnavailable as e:
                from prometheus.models.base import PaidNeedsYes

                if tried and not isinstance(e, PaidNeedsYes):
                    break                    # no other AI left to try: say what went wrong with the ones that answered
                raise                        # (only paid ones left: ask the owner)
            label = f"{reply.provider} · {reply.model}"
            try:
                plan = validate_plan(reply.text, registry)
                break
            except PlanError as e:
                retry = messages + [{"role": "assistant", "content": reply.text or "(empty)"},
                                    {"role": "user", "content": f"That reply is not valid: {e} Reply with corrected "
                                                                "JSON only."}]
                again = self.router.complete(purpose, retry, system, json_schema=PLAN_SCHEMA, max_tokens=4000,
                                             skip=skip)
                label = f"{again.provider} · {again.model}"
                try:
                    plan = validate_plan(again.text, registry)
                    break
                except PlanError as e2:
                    tried.append(label)
                    try:
                        self.store.audit("planner", "unreadable_plan", {"model": label, "error": str(e2)[:300]})
                    except Exception:
                        pass
                    skip = skip | {reply.provider, again.provider}
        if plan is None:
            raise PlanError(f"the {'AIs that plan' if len(tried) > 1 else 'AI that plans'} ({', '.join(tried)}) answered in a way I "
                            "couldn't use. Try again, or pick another Brain in Settings → AI.")
        plan.model = label
        return plan

    # -- the front door (M3.1) ------------------------------------------------------------------------------------
    def _front_door(self, chat_id: int, task_id: int, text: str, history: list[dict]) -> TaskOutcome | None:
        """Simple: a quick direct answer. Ask: one question. Complex: the think-act-look loop. Medium (or anything
        that can't be done this way): None, so the normal plan runs."""
        from prometheus.orchestrator.front_door import decide

        try:
            d = decide(self.router, text, history)
        except Exception:
            return None
        self.store.add_agent_note(task_id, "front door", f"Route: {d.route} (decided by {d.how}"
                                                         + (f": {d.why}" if d.why else "") + ")")
        if d.route == "ask":
            return self._finish(chat_id, task_id, "done", d.question, "front door")
        if d.route == "simple":
            try:
                return self._answer_directly(chat_id, task_id, text, history)
            except (ProviderUnavailable, BudgetExceeded) as e:
                return self._finish(chat_id, task_id, "paused", self._paused_text(chat_id, text, e, False))
            except Stopped as e:
                return self._finish(chat_id, task_id, "stopped", str(e))
            except Exception:
                return None                       # fall back to the normal plan
        if d.route == "complex" and self.loop is not None:
            try:
                return self.loop.run(chat_id, task_id, text, history)
            except NotImplementedError:
                return None
            except Exception as e:            # never leave a task "running" (M3 review)
                return self._finish(chat_id, task_id, "failed", f"Something went wrong part way through ({type(e).__name__})."
                                                                " Try again, or ask in smaller steps.")
        return None

    def _answer_directly(self, chat_id: int, task_id: int, text: str, history: list[dict]) -> TaskOutcome:
        """Talk and general knowledge: a quick AI answers straight away (streamed when it can), no plan."""
        system = ("You are Prometheus, a personal AI assistant on its owner's laptop. Answer the owner's message "
                  f"directly and helpfully.\n\nWriting rules:\n{self._style()}\n\n{UNTRUSTED_RULE}")
        content = self._context(text, chat_id)
        messages = list(history or []) + [{"role": "user", "content": content + f"Message: {text}"}]
        with self.router.step_kind("chat"):
            reply = self._complete_streamed(chat_id, "combine", messages, system)
        if chat_id in self.stopping:                 # Stop pressed while it was answering
            return self._finish(chat_id, task_id, "stopped", "Stopped. Ask again whenever you like.")
        self._name_chat(chat_id, text[:60])
        return self._finish(chat_id, task_id, "done", reply.text, f"{reply.provider} · {reply.model}")

    def _complete_streamed(self, chat_id: int, purpose: str, messages: list[dict], system: str, max_tokens: int = 4000):
        """The final answer, shown word by word while it is written when the AI can stream (M3.5)."""
        stream = getattr(self.router, "stream", None)
        if stream is None:
            return self.router.complete(purpose, messages, system, max_tokens=max_tokens)
        self.live_text[chat_id] = ""

        def piece(text: str | None) -> None:
            self.live_text[chat_id] = "" if text is None else self.live_text.get(chat_id, "") + text
        try:
            return stream(purpose, messages, system, on_text=piece, max_tokens=max_tokens)
        finally:
            self.live_text.pop(chat_id, None)

    def _name_chat(self, chat_id: int, proposed: str) -> None:
        """Give the chat the planner's name during its first two requests, unless the owner named it."""
        from prometheus.memory.titles import clean_title

        title = clean_title(proposed)
        if not title or self.store.chat_named_by(chat_id) == "user":
            return
        asked = sum(1 for m in self.store.messages(chat_id) if m.role == "user")
        if asked <= NAME_UNTIL and title != self.store.chat_title(chat_id):
            self.store.rename_chat(chat_id, title)

    def _finish(self, chat_id: int, task_id: int, status: str, answer: str, model: str = "",
                private: bool = False, topic: str = "") -> TaskOutcome:
        self.stopping.discard(chat_id)
        self.pausing.discard(chat_id)
        # the markers around outside text are for the AIs; the owner sees only the words
        answer = re.sub(r"</?untrusted(?: source=\"[^\"\n]*\")?>\n?", "", answer or "").replace("&lt;/untrusted", "</untrusted")
        noted = self._remembered.pop(task_id, [])
        if noted:
            answer = (answer.rstrip() + "\n\n_Remembered: " + "; ".join(noted)
                      + ". (Wrong? Forget it on the Memory page.)_")
        self.store.set_task_status(task_id, status, answer)
        self.store.add_message(chat_id, "assistant", answer, model, private, topic=topic)
        return TaskOutcome(task_id, status, answer)

    # -- the planning loop: run, check, try another way ------------------------------------
    def _spent(self) -> float:
        budget = getattr(self.router, "budget", None)
        try:
            return float(budget.state().spent) if budget is not None else 0.0
        except Exception:
            return 0.0

    def _paused_text(self, chat_id: int, text: str, e: Exception, read_only: bool) -> str:
        """What to say when no AI could answer; an AI limit also books a retry for later (prometheus/resume.py)."""
        said = str(e)
        if read_only or isinstance(e, BudgetExceeded):
            return said
        try:
            from prometheus.features import on
            from prometheus.resume import note_limit

            if on(getattr(self, "settings", None), "resume"):
                more = note_limit(self.store, chat_id, text, said)
                if more:
                    said = f"{said}\n\n{more}"
        except Exception:
            pass
        return said

    def _usable_agents(self):
        """The agents the planner may use: not those of a feature switched off in Settings → Features."""
        from prometheus.features import agent_blocked

        settings = getattr(self, "settings", None)
        return [(n, c) for n, c in self.registry.items() if not agent_blocked(settings, n)]

    def _run_step(self, task_id: int, st: dict, results: dict, private: set, label: str = ""):
        sid = self.store.add_step(task_id, st["agent"], label + st["goal"], st["inputs"])
        agent_cls = self.registry.get(st["agent"])
        try:
            if agent_cls is None:
                raise PlanError(f"Unknown agent {st['agent']}")
            from prometheus.features import agent_blocked

            blocked = agent_blocked(getattr(self, "settings", None), st["agent"])
            if blocked:
                self.store.finish_step(sid, "failed", blocked)
                return StepResult("failed", blocked), agent_cls
            if step_is_private(agent_cls, st["inputs"]):
                private.add(st["id"])
            inputs = resolve_inputs(st["inputs"], results, frozenset(private),
                                    getattr(agent_cls, "accepts_private", True))
            agent = agent_cls()
            ctx = self.make_context(task_id, sid)
            skill = self.skills.get(st.get("skill", "")) if self.skills and st.get("skill") else None
            ctx.skill = skill.body if skill else ""
            if ctx.skills is None:
                ctx.skills = self.skills
            with self.router.step_kind(st.get("kind", "")):        # the job kind picks the model (M3.4)
                res = agent.run(st["goal"], inputs, ctx)
            if skill is not None:                  # the Skills page shows when each guide was used
                res.data = {**(res.data or {}), "skill": skill.name}
        except (ProviderUnavailable, BudgetExceeded) as e:
            self.store.finish_step(sid, "paused", str(e))
            raise
        except Stopped:
            self.store.finish_step(sid, "stopped", "Stopped by the kill switch.")
            raise
        except Exception as e:  # one broken step never crashes the task
            res = StepResult("failed", f"{type(e).__name__}: {e}")
        self.store.finish_step(sid, res.status, res.summary, res.data)
        return res, agent_cls

    def _verify(self, st: dict, res: StepResult, agent_cls, task_id: int) -> str:
        """For actions: check the goal is really there. Returns why not ('' = fine or can't tell)."""
        verify = getattr(agent_cls, "verify", None)
        if verify is None:
            return ""
        try:
            ctx = self.make_context(task_id, 0)
            return verify(agent_cls(), st["goal"], st["inputs"], res, ctx) or ""
        except Exception:
            return ""                              # a check that can't run never fails the task

    def _learn_from(self, st: dict, tries: list, last: dict, res: StepResult) -> None:
        """Write down what failed and what worked, so the next similar request goes right first time. Steps that
        read personal files only record which agent and inputs were used, never what was found."""
        def short(a: dict, r: StepResult | None = None) -> str:
            ins = ", ".join(f"{k}={str(v)[:40]}" for k, v in a["inputs"].items() if not str(v).startswith("$step"))
            hidden = getattr(self.registry.get(a["agent"]), "private_outputs", False)
            why = "" if r is None or hidden else f" ({' '.join(r.summary.split())[:90]})"
            return f"{a['agent']}[{ins}]{why}"

        failed = "; ".join(short(a, r) for a, r in tries)
        if res.status == "done":
            text = f"For '{st['goal'][:80]}': {failed} failed, then {short(last)} worked. Next time start with that."
        else:
            text = f"For '{st['goal'][:80]}': tried {failed} and {short(last, res)}; none worked."
        try:
            self.store.add_lesson(text, res.status == "done")
        except Exception:
            pass

    def _repair(self, text: str, st: dict, tries: list, agent_cls, only: str = "") -> dict | None:
        """Ask the lead AI for ONE different way to reach the same goal, or None to stop."""
        usable = [(n, c) for n, c in self._usable_agents() if not only or n == only]
        from prometheus.orchestrator.registry import planner_line

        agents = "\n".join(planner_line(name, cls) for name, cls in usable)
        lines = []
        for a, r in tries:
            hidden = getattr(self.registry.get(a["agent"]), "private_outputs", False)
            why = "(details from the owner's files are hidden)" if hidden else r.summary[:500]
            lines.append(f"- {a['agent']} with goal '{a['goal']}' and inputs {json.dumps(a['inputs'])[:400]}: "
                         f"failed: {why}")
        prompt = (f"Request: {text}\n\nStep {st['id']} has to: {st['goal']}\nWhat was tried:\n"
                  + untrusted("\n".join(lines), "failed tries")
                  + "\n\nPropose ONE different step that reaches the same goal another way (another agent, other "
                    "inputs, a broader search, the website instead of the app…). Never repeat a try that failed. "
                    "If it can't be done or needs the owner, set action to give_up. Reply in JSON.")
        try:
            reply = self.router.complete("plan", [{"role": "user", "content": prompt}],
                                         f"You fix failed steps.\n\nAgents:\n{agents}\n\n{UNTRUSTED_RULE}",
                                         json_schema=REPAIR_SCHEMA, max_tokens=800)
            data = json.loads(reply.text)
        except (ProviderUnavailable, BudgetExceeded, ValueError):
            return None
        if str(data.get("action")) != "retry":
            return None
        try:
            plan = validate_plan(json.dumps({"answer": "", "remember": [], "steps": [{
                "id": 1, "agent": data.get("agent", ""), "goal": str(data.get("goal") or st["goal"]),
                "skill": "", "inputs": data.get("inputs") or [], "depends_on": []}]}), dict(usable) if only else self.registry)
        except PlanError:
            return None
        nxt = plan.steps[0]
        if any(nxt["agent"] == a["agent"] and nxt["inputs"] == a["inputs"] for a, _ in tries):
            return None                            # the same thing again: stop instead of looping
        return nxt

    def request_stop(self, chat_id: int) -> None:
        """Stop this chat's reply only (Stop all pauses everything instead). Takes effect before the next step;
        a step already running finishes first."""
        self.stopping.add(chat_id)

    def request_pause(self, chat_id: int) -> None:
        """Pause this chat's reply (the Tasks page's Pause): it stops at the next safe point and keeps what's left."""
        self.pausing.add(chat_id)

    def run(self, chat_id: int, text: str, read_only: bool = False) -> TaskOutcome:
        """One request. Paid AI used without asking (cheap models, heavy jobs) is counted for this task only."""
        with self.router.task_spending() if hasattr(self.router, "task_spending") else contextlib.nullcontext():
            return self._run(chat_id, text, read_only)

    def _run(self, chat_id: int, text: str, read_only: bool = False) -> TaskOutcome:
        """read_only: another app asked; only reading steps run, nothing is remembered or changed."""
        from prometheus.safety.budget import for_chat

        self.stopping.discard(chat_id)            # a stop pressed for an earlier reply doesn't stop this one
        self.pausing.discard(chat_id)             # nor a pause pressed for an earlier one

        for_chat(chat_id)                         # costs from this task count for this chat
        history = self._history(chat_id)          # taken before this message is added
        user_msg = self.store.add_message(chat_id, "user", text)
        task_id = self.store.create_task(chat_id, text)
        self.store.set_task_status(task_id, "running")
        for intercept in ([] if read_only else self.interceptors):     # a quiz waiting for an answer, for example
            try:
                said = intercept(chat_id, text)
            except Exception:
                said = None
            if said:
                # a quiz or exam check-in repeats a card's question and answer, which come from your notes
                return self._finish(chat_id, task_id, "done", said, "instant", private=True)
        if not read_only and "<untrusted" not in text:
            # "remember…", "update memory…", "forget…": always done, at once, without any AI
            from prometheus.remember import handle as memory_command

            try:
                done = memory_command(self.store, chat_id, text)
            except Exception:
                done = None
            if done is not None:
                said, rest = done
                if not rest:
                    return self._finish(chat_id, task_id, "done", said, "instant")
                self.store.add_message(chat_id, "assistant", said, "instant")
                text = rest                       # "…and can you also …?": carry on with the rest
        if PAID_YES.match(text or "") and not read_only and "<untrusted" not in text:
            # "yes, use paid": the last request that was waiting for it runs again, with paid AI allowed this once
            rows = self.store._rows("SELECT id, text FROM tasks WHERE chat_id = ? AND id < ? AND status = 'paused' "
                                    "AND answer LIKE '%use paid%' ORDER BY id DESC LIMIT 1", (chat_id, task_id))
            if not rows:
                return self._finish(chat_id, task_id, "done", "There's nothing waiting for a paid AI in this chat.",
                                    "instant")
            self._finish(chat_id, task_id, "done", "OK, using a paid AI for that, this once.", "instant")
            self.store.set_task_status(rows[0]["id"], "done", "Answered again with a paid AI (see below).")
            with self.router.paid_allowed():
                return self.run(chat_id, rows[0]["text"], read_only=read_only)
        if re.search(r"\bsave (?:this|that|it) as a (?:practice )?test\b", text or "", re.I) and "<untrusted" not in text:
            from prometheus.practice import from_last_task

            return self._finish(chat_id, task_id, "done", from_last_task(self.store, chat_id), "instant")
        from prometheus.quick import understand

        only = ""
        mention = MENTION.match(text or "") if not read_only and "<untrusted" not in text else None
        if mention and mention.group(1).lower() in self.registry:
            from prometheus.features import agent_blocked

            only = mention.group(1).lower()
            blocked = agent_blocked(getattr(self, "settings", None), only)
            if blocked:
                return self._finish(chat_id, task_id, "failed", f"I can't use @{only} now: {blocked}")
            text = text[mention.end():].strip() or text

        try:
            # instant phrases are for the owner's own words only, never for text that carries outside content
            from prometheus.features import on as _feature_on

            fast = (None if only or "<untrusted" in text or not _feature_on(getattr(self, "settings", None), "instant")
                    else understand(text, self.store))
        except Exception:
            fast = None
        if read_only and fast is not None and fast.action:
            return self._finish(chat_id, task_id, "failed", "That would change something, so the owner has to ask.")
        if fast is not None and fast.answer:                  # time, date, weather: no AI needed
            return self._finish(chat_id, task_id, "done", fast.answer, "instant")
        if fast is None and not only and not read_only and self.front_door:
            door = self._front_door(chat_id, task_id, text, history)
            if door is not None:
                return door
        try:
            plan = Plan(fast.steps, answer="") if fast is not None else self._plan(text, history, chat_id, only=only)
        except PlanError as e:
            return self._finish(chat_id, task_id, "failed", f"I couldn't work out the steps for that: {e}")
        except (ProviderUnavailable, BudgetExceeded) as e:
            return self._finish(chat_id, task_id, "paused", self._paused_text(chat_id, text, e, read_only))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))

        if fast is None and not read_only:
            self._remember(chat_id, plan.remember, text)
            self._save_facts(chat_id, task_id, plan.facts)
        from prometheus.memory.archive import normalize_topic

        topic = normalize_topic(plan.topic, [t["topic"] for t in self.store.topics(KNOWN_TOPICS)])
        if topic:
            self.store.set_message_topic(user_msg, topic)
        self._name_chat(chat_id, plan.title)
        if chat_id in self.stopping:
            return self._finish(chat_id, task_id, "stopped", "Stopped. Ask again whenever you like.")
        if not plan.steps:                        # no tools needed: the plan call already wrote the answer
            return self._finish(chat_id, task_id, "done", plan.answer, plan.model, topic=topic)

        if self.before_steps is not None:
            try:
                handed = self.before_steps(chat_id, text, plan, read_only)
            except Exception:
                handed = ""
            if handed:
                return self._finish(chat_id, task_id, "done", handed, "phone")
        results: dict[int, StepResult] = {}
        bad: set[int] = set()
        private: set[int] = set()
        return self._execute(chat_id, task_id, text, plan, read_only, only, fast, topic, results, bad, private)

    def _execute(self, chat_id: int, task_id: int, text: str, plan: Plan, read_only: bool, only: str,
                 fast, topic: str, results: dict[int, StepResult], bad: set[int],
                 private: set[int]) -> TaskOutcome:
        """Run the plan's steps (all of them, or the ones left after a pause) and write one answer."""
        pos = 0                                   # the step about to run; what a pause keeps
        try:
            spent_at_start = self._spent()
            executed = 0
            for pos, st in enumerate(plan.steps):
                self.killswitch.check()
                if chat_id in self.stopping:
                    raise Stopped("Stopped. Ask again whenever you like.")
                if chat_id in self.pausing:
                    raise Paused("Paused by you.")
                if any(d in bad for d in st["depends_on"]):
                    sid = self.store.add_step(task_id, st["agent"], st["goal"], st["inputs"])
                    self.store.finish_step(sid, "skipped", "Skipped because an earlier step failed.")
                    bad.add(st["id"])
                    continue
                attempt, tries = st, []
                while True:
                    if executed >= self.max_steps:
                        res = StepResult("failed", f"Stopped: this task reached the limit of {self.max_steps} steps.")
                        break
                    if self._spent() - spent_at_start >= self.task_budget:
                        res = StepResult("failed", f"Stopped: this task reached its ${self.task_budget:.2f} spending "
                                                   "limit. Ask again if you want me to keep going.")
                        break
                    executed += 1
                    if read_only and not read_only_ok(attempt):
                        sid = self.store.add_step(task_id, attempt["agent"], attempt["goal"], attempt["inputs"])
                        res = StepResult("failed", "Not allowed: another app asked, and this would change something.")
                        self.store.finish_step(sid, "failed", res.summary)
                        break
                    res, agent_cls = self._run_step(task_id, attempt, results, private,
                                                    label=f"Try {len(tries) + 1}: " if tries else "")
                    if res.status == "done":
                        check = self._verify(attempt, res, agent_cls, task_id)
                        if check:                          # it said done, but the goal isn't there
                            res = StepResult("failed", check, res.data)
                    if res.status != "failed" or len(tries) + 1 >= self.step_tries:
                        break
                    tries.append((attempt, res))
                    nxt = self._repair(text, st, tries, agent_cls, only=only)
                    if nxt is None:
                        break
                    attempt = {**nxt, "id": st["id"], "depends_on": st["depends_on"]}
                results[st["id"]] = res
                if tries:
                    self._learn_from(st, tries, attempt, res)
                    note = "; ".join(f"{a['agent']} ({a['goal'][:60]}): failed" for a, _ in tries)
                    self.store.add_agent_note(task_id, "planner", f"Retried step {st['id']}: {note}. "
                                                                  f"Final: {res.status}.")
                if res.status == "failed":
                    bad.add(st["id"])
        except (ProviderUnavailable, BudgetExceeded) as e:
            return self._finish(chat_id, task_id, "paused", self._paused_text(chat_id, text, e, read_only))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))
        except Paused:
            self._keep_for_carry_on(task_id, text, plan, pos, results, bad, private, read_only, only, topic)
            return self._finish(chat_id, task_id, "paused",
                                "Paused. Everything it needed is done; press Carry on and I'll write the answer.")

        if chat_id in self.pausing:                # pressed while the last step ran: keep it for Carry on
            self._keep_for_carry_on(task_id, text, plan, len(plan.steps), results, bad, private, read_only, only,
                                    topic)
            return self._finish(chat_id, task_id, "paused",
                                "Paused. Everything it needed is done; press Carry on and I'll write the answer.")

        if fast is not None:                                  # an instant command: say what the step said
            steps = self.store.steps(task_id)
            answer = "\n".join(s.result for s in steps if s.result) or "Done."
            status = "failed" if bad else "done"
            return self._finish(chat_id, task_id, status, answer, "instant", private=bool(private))
        written_by = ""
        report = "\n\n".join(
            f"Step {s.id} ({s.agent}, {s.status}):\n" + untrusted(s.result, s.agent) for s in self.store.steps(task_id))
        # If any step read personal files, the answer is written by a local model only (or not by a model at all).
        purpose = "private" if private else "combine"
        try:
            reply = self._complete_streamed(chat_id, purpose,
                                            [{"role": "user", "content": f"Request: {text}\n\n{report}"}],
                                            f"{self._prompt('combine')}\n\nWriting rules:\n{self._style()}\n\n"
                                            f"{UNTRUSTED_RULE}", max_tokens=3000)
            answer = reply.text.strip()
            written_by = f"{reply.provider} · {reply.model}"
        except (ProviderUnavailable, BudgetExceeded):
            answer = "\n".join(f"- {s.agent}: {s.status}. {s.result}" for s in self.store.steps(task_id))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))
        status = "failed" if bad and len(bad) == len(plan.steps) else "done"
        return self._finish(chat_id, task_id, status, answer or "Done.", written_by, private=bool(private),
                            topic=topic)

    def _keep_for_carry_on(self, task_id: int, text: str, plan: Plan, pos: int, results: dict[int, StepResult],
                           bad: set[int], private: set[int], read_only: bool, only: str, topic: str) -> None:
        """Write down what's left of a paused task, so Carry on runs only that (never the whole request again)."""
        try:
            save_paused_run(self.store, task_id, {
                "text": text, "topic": topic, "read_only": bool(read_only), "only": only or "",
                "steps": list(plan.steps[pos:]),
                "done": {str(k): asdict(v) for k, v in results.items()},
                "bad": sorted(bad), "private": sorted(private)})
        except (TypeError, ValueError):
            pass            # a step's answer can't be written down: Carry on says so, and Run again still works

    def resume_paused(self, task_id: int) -> TaskOutcome | None:
        """Carry on a task the owner paused, from the step it stopped at. None when nothing was kept."""
        saved = paused_run(self.store, task_id)
        t = self.store.task(task_id)
        if saved is None or not t:
            drop_paused_run(self.store, task_id)
            return None
        drop_paused_run(self.store, task_id)          # taken now: a double click can't run it twice
        if saved.get("loop") and self.loop is not None:     # a think-act-look loop carries on from its round (M3 B1)
            return self.loop.resume(task_id, saved)
        chat_id = int(t["chat_id"])
        self.stopping.discard(chat_id)
        self.pausing.discard(chat_id)
        self.store.set_task_status(task_id, "running")
        self.store.add_message(chat_id, "assistant", "Carrying on from where I stopped.", "paused")
        plan = Plan(steps=list(saved.get("steps") or []), topic=str(saved.get("topic") or ""))
        results = {int(k): StepResult(**v) for k, v in (saved.get("done") or {}).items() if isinstance(v, dict)}
        return self._execute(chat_id, task_id, str(saved.get("text") or t["text"]), plan,
                             bool(saved.get("read_only")), str(saved.get("only") or ""), None,
                             str(saved.get("topic") or ""), results, set(saved.get("bad") or []),
                             set(saved.get("private") or []))
