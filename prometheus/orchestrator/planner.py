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
        },
        "required": ["id", "agent", "goal", "skill", "inputs", "depends_on"],
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


class PlanError(Exception):
    pass


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
        clean.append({"id": sid, "agent": s["agent"], "goal": str(s.get("goal", "")),
                      "skill": str(s.get("skill") or "").strip(),
                      "inputs": inputs, "depends_on": list(deps)})
        seen.add(sid)
    return Plan(clean, answer=answer, remember=remember, topic=topic, title=title, facts=facts)


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
        self.step_tries, self.task_budget, self.max_steps = STEP_TRIES, TASK_BUDGET, MAX_TASK_STEPS
        self.extra_catalog = None          # callable: tools of connected apps, for the planner
        self.extra_sections: list = []     # (heading, callable returning text) added to the planner's system
        self.interceptors: list = []       # callables (chat_id, text) -> answer or None: e.g. a quiz in progress
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

    def _save_facts(self, chat_id: int, task_id: int, facts: list[dict]) -> None:
        """Lasting facts from the owner's message are saved by themselves and listed under the answer."""
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

    def _plan(self, text: str, history: list[dict] | None = None, chat_id: int | None = None) -> Plan:
        agents = "\n".join(f"- {name}: {cls.description}" for name, cls in self.registry.items())
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
        relevant = [h.text for h in self.store.recall(text, limit=8, kinds=("memory", "chat_note"))]
        newest = [m.text for m in self.store.memories()[-3:]]
        remembered = list(dict.fromkeys(relevant + newest))
        memories = "\n".join(f"- {m[:400]}" for m in remembered) or "(none)"
        from prometheus.quick import now_line

        content = f"{now_line()}\n\nThings to remember about the user:\n{memories}\n\n"
        known_facts = self.facts.relevant(text)
        if known_facts:
            content += "Known facts (people, places and things in the owner's life):\n" + "\n".join(
                f"- {f}" for f in known_facts) + "\n\n"
        # Passages from web pages read before (never private files: this goes to the planning model).
        content += self._earlier_chats(text, chat_id)
        lessons = self.store.relevant_lessons(text)
        if lessons:
            content += "Lessons from earlier tasks (follow them):\n" + "\n".join(f"- {l}" for l in lessons) + "\n\n"
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

    def _run_step(self, task_id: int, st: dict, results: dict, private: set, label: str = ""):
        sid = self.store.add_step(task_id, st["agent"], label + st["goal"], st["inputs"])
        agent_cls = self.registry.get(st["agent"])
        try:
            if agent_cls is None:
                raise PlanError(f"Unknown agent {st['agent']}")
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

    def _repair(self, text: str, st: dict, tries: list, agent_cls) -> dict | None:
        """Ask the lead AI for ONE different way to reach the same goal, or None to stop."""
        agents = "\n".join(f"- {name}: {cls.description}" for name, cls in self.registry.items())
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
                "skill": "", "inputs": data.get("inputs") or [], "depends_on": []}]}), self.registry)
        except PlanError:
            return None
        nxt = plan.steps[0]
        if any(nxt["agent"] == a["agent"] and nxt["inputs"] == a["inputs"] for a, _ in tries):
            return None                            # the same thing again: stop instead of looping
        return nxt

    def run(self, chat_id: int, text: str) -> TaskOutcome:
        from prometheus.safety.budget import for_chat

        for_chat(chat_id)                         # costs from this task count for this chat
        history = self._history(chat_id)          # taken before this message is added
        user_msg = self.store.add_message(chat_id, "user", text)
        task_id = self.store.create_task(chat_id, text)
        self.store.set_task_status(task_id, "running")
        for intercept in self.interceptors:            # a quiz waiting for an answer, for example
            try:
                said = intercept(chat_id, text)
            except Exception:
                said = None
            if said:
                return self._finish(chat_id, task_id, "done", said, "instant")
        from prometheus.quick import understand

        try:
            fast = understand(text, self.store)
        except Exception:
            fast = None
        if fast is not None and fast.answer:                  # time, date, weather: no AI needed
            return self._finish(chat_id, task_id, "done", fast.answer, "instant")
        try:
            plan = Plan(fast.steps, answer="") if fast is not None else self._plan(text, history, chat_id)
        except PlanError as e:
            return self._finish(chat_id, task_id, "failed", f"I couldn't make a working plan for that: {e}")
        except (ProviderUnavailable, BudgetExceeded) as e:
            return self._finish(chat_id, task_id, "paused", str(e))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))

        if fast is None:
            self._remember(chat_id, plan.remember, text)
            self._save_facts(chat_id, task_id, plan.facts)
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
            spent_at_start = self._spent()
            executed = 0
            for st in plan.steps:
                self.killswitch.check()
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
                    res, agent_cls = self._run_step(task_id, attempt, results, private,
                                                    label=f"Try {len(tries) + 1}: " if tries else "")
                    if res.status == "done":
                        check = self._verify(attempt, res, agent_cls, task_id)
                        if check:                          # it said done, but the goal isn't there
                            res = StepResult("failed", check, res.data)
                    if res.status != "failed" or len(tries) + 1 >= self.step_tries:
                        break
                    tries.append((attempt, res))
                    nxt = self._repair(text, st, tries, agent_cls)
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
            return self._finish(chat_id, task_id, "paused", str(e))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))

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
            reply = self.router.complete(purpose, [{"role": "user", "content": f"Request: {text}\n\n{report}"}],
                                         f"{self._prompt('combine')}\n\nWriting rules:\n{self._style()}\n\n{UNTRUSTED_RULE}",
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
