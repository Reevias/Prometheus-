"""Lead AI (HuggingGPT pattern): plan -> pick agent -> run steps -> combine one answer."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prometheus.agents.base import UNTRUSTED_RULE, AgentContext, StepResult, untrusted
from prometheus.models.base import ProviderUnavailable
from prometheus.safety.budget import BudgetExceeded
from prometheus.safety.killswitch import Stopped

MAX_STEPS = 8
PACKAGE_PROMPTS = Path(__file__).resolve().parent.parent / "prompts"
REF = re.compile(r"^\$step(\d+)\.(\w+)$")

PLAN_SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "agent": {"type": "string"},
            "goal": {"type": "string"},
            "inputs": {"type": "array", "items": {
                "type": "object",
                "properties": {"key": {"type": "string"}, "value": {"type": "string"}},
                "required": ["key", "value"],
                "additionalProperties": False,
            }},
            "depends_on": {"type": "array", "items": {"type": "integer"}},
        },
        "required": ["id", "agent", "goal", "inputs", "depends_on"],
        "additionalProperties": False,
    }}},
    "required": ["steps"],
    "additionalProperties": False,
}


class PlanError(Exception):
    pass


@dataclass
class Plan:
    steps: list[dict]


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
        steps = data["steps"]
    except (ValueError, KeyError, TypeError) as e:
        raise PlanError(f"The plan is not valid JSON with a 'steps' list ({e}).") from e
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        raise PlanError(f"The plan must have 1 to {MAX_STEPS} steps.")
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
        clean.append({"id": sid, "agent": s["agent"], "goal": str(s.get("goal", "")),
                      "inputs": _inputs(s.get("inputs", {})), "depends_on": list(deps)})
        seen.add(sid)
    return Plan(clean)


def resolve_inputs(inputs: dict, results: dict[int, StepResult], private_steps: frozenset[int] = frozenset(),
                   allow_private: bool = True) -> dict:
    out = {}
    for key, value in inputs.items():
        m = REF.match(value) if isinstance(value, str) else None
        if m:
            sid, field = int(m.group(1)), m.group(2)
            if sid in private_steps and not allow_private:
                raise PlanError(f"Step {sid} holds private data from your files; it can't be sent to the web or cloud.")
            if sid not in results or field not in results[sid].data:
                raise PlanError(f"Step result {value} does not exist.")
            out[key] = results[sid].data[field]
        else:
            out[key] = value
    return out


class Orchestrator:
    def __init__(self, store, router, registry: dict, make_context: Callable[[int, int], AgentContext],
                 killswitch, prompts_dir: Path | None = None):
        self.store = store
        self.router = router
        self.registry = registry
        self.make_context = make_context
        self.killswitch = killswitch
        self.prompts_dir = Path(prompts_dir) if prompts_dir else PACKAGE_PROMPTS

    def _prompt(self, name: str) -> str:
        path = self.prompts_dir / f"{name}.md"
        if not path.exists():
            path = PACKAGE_PROMPTS / f"{name}.md"
        return path.read_text(encoding="utf-8")

    def _plan(self, text: str) -> Plan:
        agents = "\n".join(f"- {name}: {cls.description}" for name, cls in self.registry.items())
        system = f"{self._prompt('planner')}\n\nAgents:\n{agents}\n\n{UNTRUSTED_RULE}"
        memories = "\n".join(f"- {m.text}" for m in self.store.memories()[-10:]) or "(none)"
        messages = [{"role": "user", "content": f"Things to remember about the user:\n{memories}\n\nRequest: {text}"}]
        reply = self.router.complete("plan", messages, system, json_schema=PLAN_SCHEMA, max_tokens=2000)
        try:
            return validate_plan(reply.text, self.registry)
        except PlanError as e:
            messages += [{"role": "assistant", "content": reply.text or "(empty)"},
                         {"role": "user", "content": f"That plan is not valid: {e} Reply with a corrected JSON plan only."}]
            reply = self.router.complete("plan", messages, system, json_schema=PLAN_SCHEMA, max_tokens=2000)
            return validate_plan(reply.text, self.registry)

    def _finish(self, chat_id: int, task_id: int, status: str, answer: str, model: str = "") -> TaskOutcome:
        self.store.set_task_status(task_id, status, answer)
        self.store.add_message(chat_id, "assistant", answer, model)
        return TaskOutcome(task_id, status, answer)

    def run(self, chat_id: int, text: str) -> TaskOutcome:
        self.store.add_message(chat_id, "user", text)
        task_id = self.store.create_task(chat_id, text)
        self.store.set_task_status(task_id, "running")
        try:
            plan = self._plan(text)
        except PlanError as e:
            return self._finish(chat_id, task_id, "failed", f"I couldn't make a working plan for that: {e}")
        except (ProviderUnavailable, BudgetExceeded) as e:
            return self._finish(chat_id, task_id, "paused", str(e))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))

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
                    res = agent.run(st["goal"], inputs, self.make_context(task_id, sid))
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
        try:
            reply = self.router.complete("combine", [{"role": "user", "content": f"Request: {text}\n\n{report}"}],
                                         f"{self._prompt('combine')}\n\n{UNTRUSTED_RULE}", max_tokens=1500)
            answer = reply.text.strip()
            written_by = f"{reply.provider} · {reply.model}"
        except (ProviderUnavailable, BudgetExceeded):
            answer = "\n".join(f"- {s.agent}: {s.status}. {s.result}" for s in self.store.steps(task_id))
        except Stopped as e:
            return self._finish(chat_id, task_id, "stopped", str(e))
        status = "failed" if bad and len(bad) == len(plan.steps) else "done"
        return self._finish(chat_id, task_id, status, answer or "Done.", written_by)
