"""Lead AI (HuggingGPT pattern): plan -> pick agent -> run steps -> combine one answer."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from prometheus.agents.base import UNTRUSTED_RULE, AgentContext, StepResult, untrusted
from prometheus.models.base import ProviderUnavailable
from prometheus.safety.budget import BudgetExceeded
from prometheus.safety.killswitch import Stopped

MAX_STEPS = 8
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
SUMMARY_EVERY = 10
MAX_REMEMBER = 3
ASKED_TO_REMEMBER = re.compile(r"\b(remember|don'?t forget|do not forget|keep in mind|note that|save this|from now on)\b", re.I)
SECRETISH = re.compile(r"(sk-[A-Za-z0-9_-]{6,}|ghp_|github_pat_|xox[bp]-|AKIA[0-9A-Z]{8,}|pass(word|phrase)|passwd|secret|"
                       r"api[ _-]?key|token\b|\bpin\b|\b[A-Za-z0-9+/_-]{32,}\b)", re.I)
PRIVATE_PLACEHOLDER = "(an answer with details from your files, written on your laptop and not shown here)"

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}, "topic": {"type": "string"}, "title": {"type": "string"},
                   "remember": {"type": "array", "items": {"type": "string"}},
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
        },
        "required": ["id", "agent", "goal", "skill", "inputs", "depends_on"],
        "additionalProperties": False,
    }}},
    "required": ["answer", "topic", "title", "remember", "steps"],
    "additionalProperties": False,
}


class PlanError(Exception):
    pass


@dataclass
class Plan:
    steps: list[dict]
    answer: str = ""                                   # a direct reply (no tools needed)
    remember: list[str] = field(default_factory=list)  # facts the user asked to remember
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


def validate_plan(raw: str, registry: dict) -> Plan:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`").split("\n", 1)[-1]
    try:
        data = json.loads(text)
        steps = data.get("steps") or []
        answer = str(data.get("answer") or "").strip()
        remember = [str(r) for r in (data.get("remember") or []) if isinstance(r, (str, int, float))]
        topic = str(data.get("topic") or "")
        title = str(data.get("title") or "")
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
        clean.append({"id": sid, "agent": s["agent"], "goal": str(s.get("goal", "")),
                      "skill": str(s.get("skill") or "").strip(),
                      "inputs": inputs, "depends_on": list(deps)})
        seen.add(sid)
    return Plan(clean, answer=answer, remember=remember, topic=topic, title=title)


def resolve_inputs(inputs: dict, results: dict[int, StepResult], private_steps: frozenset[int] = frozenset(),
                   allow_private: bool = True) -> dict:
    out = {}
    for key, value in inputs.items():
        m = REF.match(value) if isinstance(value, str) else None
        if m:
            sid, field = int(m.group(1)), m.group(2)
            if sid in private_steps and not allow_private:
                raise PlanError(f"Step {sid} holds private data from your files; it can't be sent to the web or cloud.")
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
        self.skills = skills

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
                turns.append({"role": m.role, "content": (PRIVATE_PLACEHOLDER if m.private else m.text)[:HISTORY_CHARS]})
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

    def _plan(self, text: str, history: list[dict] | None = None, chat_id: int | None = None) -> Plan:
        agents = "\n".join(f"- {name}: {cls.description}" for name, cls in self.registry.items())
        system = f"{self._prompt('planner')}\n\nWriting rules:\n{self._prompt('style')}\n\nAgents:\n{agents}"
        catalog = self.skills.catalog_text() if self.skills else ""
        if catalog:
            system += ("\n\nSkills (how-to guides). For each step set \"skill\" to the name of the one guide that "
                       f"fits, or \"\" if none does:\n{catalog}")
        known = [t["topic"] for t in self.store.topics(KNOWN_TOPICS)]
        system += ("\n\nTopic: set \"topic\" to 1 to 3 lowercase words naming what this request is about (for example "
                   "weather, taxes, python learning). Reuse one of these earlier topics when it fits: "
                   + (", ".join(known) if known else "(none yet)"))
        system += ("\n\nTitle: set \"title\" to a short, specific name for this conversation (2 to 6 words, "
                   "for example \"Delhi weather this week\" or \"Fix Python loop error\"; no quotes, no full stop). "
                   "Name what the conversation is about, not the greeting.")
        system += f"\n\n{UNTRUSTED_RULE}"
        relevant = [h.text for h in self.store.recall(text, limit=8, kinds=("memory", "chat_note"))]
        newest = [m.text for m in self.store.memories()[-3:]]
        remembered = list(dict.fromkeys(relevant + newest))
        memories = "\n".join(f"- {m[:400]}" for m in remembered) or "(none)"
        content = f"Things to remember about the user:\n{memories}\n\n"
        # Passages from web pages read before (never private files: this goes to the planning model).
        content += self._earlier_chats(text, chat_id)
        passages = self.store.search_chunks(text, limit=PASSAGES, include_private=False, chats=False)
        if passages:
            content += "Saved passages that may help:\n" + "\n".join(
                untrusted(h.match[:PASSAGE_CHARS], h.source or h.title) for h in passages) + "\n\n"
        messages = list(history or []) + [{"role": "user", "content": content + f"Request: {text}"}]
        reply = self.router.complete("plan", messages, system, json_schema=PLAN_SCHEMA, max_tokens=4000)
        label = f"{reply.provider} · {reply.model}"
        try:
            plan = validate_plan(reply.text, self.registry)
        except PlanError as e:
            messages += [{"role": "assistant", "content": reply.text or "(empty)"},
                         {"role": "user", "content": f"That reply is not valid: {e} Reply with corrected JSON only."}]
            reply = self.router.complete("plan", messages, system, json_schema=PLAN_SCHEMA, max_tokens=4000)
            label = f"{reply.provider} · {reply.model}"
            plan = validate_plan(reply.text, self.registry)
        plan.model = label
        return plan

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
        self.store.set_task_status(task_id, status, answer)
        self.store.add_message(chat_id, "assistant", answer, model, private, topic=topic)
        return TaskOutcome(task_id, status, answer)

    def run(self, chat_id: int, text: str) -> TaskOutcome:
        history = self._history(chat_id)          # taken before this message is added
        user_msg = self.store.add_message(chat_id, "user", text)
        task_id = self.store.create_task(chat_id, text)
        self.store.set_task_status(task_id, "running")
        try:
            plan = self._plan(text, history, chat_id)
        except PlanError as e:
            return self._finish(chat_id, task_id, "failed", f"I couldn't make a working plan for that: {e}")
        except (ProviderUnavailable, BudgetExceeded) as e:
            return self._finish(chat_id, task_id, "paused", str(e))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))

        self._remember(chat_id, plan.remember, text)
        from prometheus.memory.archive import normalize_topic

        topic = normalize_topic(plan.topic, [t["topic"] for t in self.store.topics(KNOWN_TOPICS)])
        if topic:
            self.store.set_message_topic(user_msg, topic)
        self._name_chat(chat_id, plan.title)
        if not plan.steps:                        # no tools needed: the plan call already wrote the answer
            return self._finish(chat_id, task_id, "done", plan.answer, plan.model, topic=topic)

        results: dict[int, StepResult] = {}
        bad: set[int] = set()
        private: set[int] = set()
        try:
            for st in plan.steps:
                self.killswitch.check()
                if any(d in bad for d in st["depends_on"]):
                    sid = self.store.add_step(task_id, st["agent"], st["goal"], st["inputs"])
                    self.store.finish_step(sid, "skipped", "Skipped because an earlier step failed.")
                    bad.add(st["id"])
                    continue
                sid = self.store.add_step(task_id, st["agent"], st["goal"], st["inputs"])
                try:
                    agent_cls = self.registry[st["agent"]]
                    if agent_cls.private_outputs:
                        private.add(st["id"])
                    inputs = resolve_inputs(st["inputs"], results, frozenset(private),
                                            getattr(agent_cls, "accepts_private", True))
                    agent = agent_cls()
                    ctx = self.make_context(task_id, sid)
                    skill = self.skills.get(st.get("skill", "")) if self.skills and st.get("skill") else None
                    ctx.skill = skill.body if skill else ""
                    if ctx.skills is None:
                        ctx.skills = self.skills
                    res = agent.run(st["goal"], inputs, ctx)
                except (ProviderUnavailable, BudgetExceeded) as e:
                    self.store.finish_step(sid, "paused", str(e))
                    raise
                except Stopped:
                    self.store.finish_step(sid, "stopped", "Stopped by the kill switch.")
                    raise
                except Exception as e:  # one broken step never crashes the task
                    res = StepResult("failed", f"{type(e).__name__}: {e}")
                self.store.finish_step(sid, res.status, res.summary, res.data)
                results[st["id"]] = res
                if res.status == "failed":
                    bad.add(st["id"])
        except (ProviderUnavailable, BudgetExceeded) as e:
            return self._finish(chat_id, task_id, "paused", str(e))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))

        written_by = ""
        report = "\n\n".join(
            f"Step {s.id} ({s.agent}, {s.status}):\n" + untrusted(s.result, s.agent) for s in self.store.steps(task_id))
        # If any step read personal files, the answer is written by a local model only (or not by a model at all).
        purpose = "private" if private else "combine"
        try:
            reply = self.router.complete(purpose, [{"role": "user", "content": f"Request: {text}\n\n{report}"}],
                                         f"{self._prompt('combine')}\n\nWriting rules:\n{self._prompt('style')}\n\n{UNTRUSTED_RULE}",
                                         max_tokens=3000)
            answer = reply.text.strip()
            written_by = f"{reply.provider} · {reply.model}"
        except (ProviderUnavailable, BudgetExceeded):
            answer = "\n".join(f"- {s.agent}: {s.status}. {s.result}" for s in self.store.steps(task_id))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))
        status = "failed" if bad and len(bad) == len(plan.steps) else "done"
        return self._finish(chat_id, task_id, status, answer or "Done.", written_by, private=bool(private),
                            topic=topic)
