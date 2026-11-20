"""The think → act → look loop (M3.3), for complex jobs (the front door sends them here).

Each round the AI looks at what has happened so far and either takes ONE action (an agent with inputs) or gives the
final answer. The action runs through the same step runner as plans, so approvals, privacy, Stop all, Pause, feature
switches and spending limits all still apply, and every round shows on the task as a visible step with a short note
of why (never hidden reasoning). At most 12 rounds (owner); then it writes what it has.

Privacy: once a round reads the owner's files, the rest of the thinking and the answer are done by the laptop's own
AI only. If that AI isn't available, the loop stops and shows what it found without sending it anywhere.
"""
from __future__ import annotations

import json

from prometheus.agents.base import UNTRUSTED_RULE, StepResult, untrusted
from prometheus.models.base import ProviderUnavailable

MAX_ROUNDS = 12
OBSERVATION_CHARS = 3000

LOOP_SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"},
        "done": {"type": "boolean"},
        "answer": {"type": "string"},
        "agent": {"type": "string"},
        "goal": {"type": "string"},
        "kind": {"type": "string", "enum": ["chat", "plan", "research", "code", "vision", "summarise", ""]},
        "inputs": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"],
            "additionalProperties": False}},
    },
    "required": ["thought", "done", "answer", "agent", "goal", "kind", "inputs"],
    "additionalProperties": False,
}
LOOP_RULES = """
WORKING STEP BY STEP. In this mode, ignore the "steps" format above. You do the job one action at a time: each reply
is EITHER one action OR the final answer. After each action you are told what happened, then you decide the next one.
Reply with JSON only:
{"thought": one short sentence the owner will see (why this action, or why you're done),
 "done": true when you give the final answer, else false,
 "answer": the final answer when done (following the writing rules), else "",
 "agent": the agent for the next action (from the list), else "",
 "goal": what that action should achieve, else "",
 "kind": the job kind (research, code, summarise, vision, plan, chat), else "",
 "inputs": [{"key": ..., "value": ...}] for the action, else []}
Use the results you were given: a later action may use an earlier one as $roundN.key (e.g. $round1.paths).
Stop as soon as you have what the owner asked for. Never repeat an action that already worked."""


class Paused(Exception):
    pass


class ThinkActLoop:
    def __init__(self, orchestrator, max_rounds: int = MAX_ROUNDS):
        self.o = orchestrator
        self.max_rounds = max_rounds

    def _system(self) -> str:
        from prometheus.orchestrator.registry import planner_line

        agents = "\n".join(planner_line(n, c) for n, c in self.o._usable_agents())
        return (f"{self.o._prompt('planner')}\n\nWriting rules:\n{self.o._style()}\n\nAgents:\n{agents}\n"
                f"{LOOP_RULES}\n\n{UNTRUSTED_RULE}")

    def _think(self, messages: list[dict], system: str, private: bool) -> dict:
        reply = self.o.router.complete("private" if private else "plan", list(messages), system, json_schema=LOOP_SCHEMA,
                                       max_tokens=3000)
        from prometheus.orchestrator.planner import json_part

        try:
            data = json.loads(json_part(reply.text))
            return data if isinstance(data, dict) else {}
        except ValueError:
            return {"_bad": reply.text}

    def run(self, chat_id: int, task_id: int, text: str, history: list[dict]) -> "TaskOutcome":  # noqa: F821
        o = self.o
        messages = list(history or []) + [{"role": "user", "content": o._context(text, chat_id) + f"Request: {text}"}]
        return self._go(chat_id, task_id, text, messages, {}, set(), [], 1)

    def resume(self, task_id: int, saved: dict) -> "TaskOutcome":  # noqa: F821
        """Carry on a loop that was paused, or that waited for a yes, from the round it had reached (B1)."""
        from prometheus.agents.base import StepResult as _SR

        o = self.o
        t = o.store.task(task_id)
        chat_id = int(t["chat_id"])
        st = saved.get("loop") or {}
        o.stopping.discard(chat_id)
        o.pausing.discard(chat_id)
        o.store.set_task_status(task_id, "running")
        o.store.add_message(chat_id, "assistant", "Carrying on from where I stopped.", "paused")
        messages = list(st.get("messages") or [])
        if st.get("observe"):                       # a round was waiting for a yes: tell the AI what came of it
            messages.append({"role": "user", "content": self._after_yes(st)})
        results = {int(k): _SR(**v) for k, v in (st.get("results") or {}).items() if isinstance(v, dict)}
        return self._go(chat_id, task_id, str(st.get("text") or t["text"]), messages, results,
                        set(int(x) for x in st.get("private") or []), list(st.get("seen") or []),
                        int(st.get("next") or 1))

    def _after_yes(self, st: dict) -> str:
        said = []
        for aid in st.get("waiting") or []:
            outcome = (st.get("outcomes") or {}).get(str(aid))
            if outcome:
                said.append(f"The owner answered {outcome['status']}: {str(outcome['result'])[:500]}")
            else:
                said.append("The owner has not answered yet.")
        return (f"Round {int(st.get('next') or 1) - 1} was waiting for the owner's yes. "
                + " ".join(said) + " Carry on from here (never repeat an action that already worked).")

    def _keep(self, task_id: int, text: str, messages: list[dict], nxt: int, results: dict, private: set,
              seen: list[str], waiting: list[int] | None = None, observe: bool = False) -> None:
        """Write down where the loop is, so Carry on (or the yes) continues it instead of starting again."""
        from dataclasses import asdict

        from prometheus.orchestrator.planner import save_paused_run

        try:
            save_paused_run(self.o.store, task_id, {
                "text": text, "steps": [], "loop": {
                    "text": text, "messages": messages, "next": nxt, "observe": observe,
                    "results": {str(k): asdict(v) for k, v in results.items()}, "private": sorted(private),
                    "seen": seen, "waiting": list(waiting or []), "outcomes": {}}})
        except (TypeError, ValueError):
            pass            # something can't be written down: Run again still works

    def _go(self, chat_id: int, task_id: int, text: str, messages: list[dict], results: dict, private: set,
            seen: list[str], first_round: int) -> "TaskOutcome":  # noqa: F821
        from prometheus.orchestrator.planner import JOB_KINDS, PlanError, Stopped, _inputs
        from prometheus.safety.budget import BudgetExceeded

        o = self.o
        system = self._system()
        spent_at_start = o._spent()
        try:
            for n in range(first_round, self.max_rounds + 1):
                o.killswitch.check()
                if chat_id in o.stopping:
                    return o._finish(chat_id, task_id, "stopped", "Stopped. Ask again whenever you like.")
                if chat_id in o.pausing:
                    self._keep(task_id, text, messages, n, results, private, seen)
                    return o._finish(chat_id, task_id, "paused", self._so_far(seen, "Paused. Press Carry on and I'll "
                                                                                    "start from here."))
                if o._spent() - spent_at_start >= o.task_budget:
                    return o._finish(chat_id, task_id, "failed", self._so_far(
                        seen, f"Stopped: this task reached its ${o.task_budget:.2f} spending limit."))
                try:
                    with o.activity(chat_id, "thinking", f"Planning round {n}"):
                        step = self._think(messages, system, bool(private))
                except ProviderUnavailable:
                    if private:                  # never send what was read from your files to an online AI
                        return o._finish(chat_id, task_id, "done", self._so_far(
                            seen, "I read your files, but the laptop's own AI isn't available to finish, so here is "
                                  "what I found (nothing was sent online):"), private=True)
                    raise
                if "_bad" in step:
                    messages += [{"role": "assistant", "content": str(step["_bad"])[:2000]},
                                 {"role": "user", "content": "That wasn't valid JSON in the format asked. Reply again."}]
                    continue
                thought = " ".join(str(step.get("thought") or "").split())[:240]
                if thought:
                    o.store.add_agent_note(task_id, "loop", f"Round {n}: {thought}")
                if step.get("done") or (str(step.get("answer") or "").strip() and not step.get("agent")):
                    answer = str(step.get("answer") or "").strip() or self._so_far(seen, "Done.")
                    return o._finish(chat_id, task_id, "done", answer, "loop", private=bool(private))
                agent = str(step.get("agent") or "")
                messages.append({"role": "assistant", "content": json.dumps(step)[:4000]})
                if agent not in dict(o._usable_agents()):
                    messages.append({"role": "user", "content": f"There is no agent '{agent}' you can use. "
                                                                "Pick one from the list, or give the answer."})
                    continue
                try:
                    inputs = _round_refs(_inputs(step.get("inputs") or []))
                except PlanError as e:               # a badly shaped reply costs a round, not the task
                    messages.append({"role": "user", "content": f"Those inputs weren't usable: {e} Try again."})
                    continue
                kind = str(step.get("kind") or "")
                st = {"id": n, "agent": agent, "goal": str(step.get("goal") or "")[:300], "skill": "",
                      "inputs": inputs, "depends_on": [], "kind": kind if kind in JOB_KINDS else ""}
                last_approval = self._last_approval()
                with o.activity(chat_id, "acting", f"Running round {n}"):
                    res, _cls = o._run_step(task_id, st, results, private, label=f"Round {n}: ")
                results[n] = res
                seen.append(f"Round {n} ({agent}): {res.summary[:300]}")
                if res.status == "waiting_approval":
                    # not finished: it keeps its place and carries on after the yes (or Carry on)
                    waiting = self._new_pending(last_approval)
                    self._keep(task_id, text, messages, n + 1, results, private, seen, waiting, observe=True)
                    return o._finish(chat_id, task_id, "paused", res.summary, "loop", private=bool(private))
                observation = (f"Round {n} result ({agent}, {res.status}):\n"
                               + untrusted(res.summary[:OBSERVATION_CHARS], agent))
                if res.data:
                    keys = ", ".join(sorted(k for k in res.data if not k.startswith("_")))[:300]
                    if keys:
                        observation += f"\nData you can use: $round{n}.<key> for key in: {keys}"
                messages.append({"role": "user", "content": observation})
            # out of rounds: write what it has
            messages.append({"role": "user", "content": "That was the last round. Give the final answer now with "
                                                        "what you have (done: true), and say what is still missing."})
            step = self._think(messages, system, bool(private))
            answer = str(step.get("answer") or "").strip() or self._so_far(seen, "I reached the limit of rounds.")
            return o._finish(chat_id, task_id, "done", answer, "loop", private=bool(private))
        except Stopped as e:
            return o._finish(chat_id, task_id, "stopped", str(e))
        except (ProviderUnavailable, BudgetExceeded) as e:
            return o._finish(chat_id, task_id, "paused", o._paused_text(chat_id, text, e, False))

    def _last_approval(self) -> int:
        try:
            return int(self.o.store._rows("SELECT COALESCE(MAX(id), 0) AS m FROM approvals")[0]["m"])
        except Exception:
            return 0

    def _new_pending(self, after_id: int) -> list[int]:
        try:
            return [int(r["id"]) for r in self.o.store._rows(
                "SELECT id FROM approvals WHERE id > ? AND status = 'pending' ORDER BY id", (after_id,))]
        except Exception:
            return []

    @staticmethod
    def _so_far(seen: list[str], head: str) -> str:
        return head + ("\n\n" + "\n".join(f"- {s}" for s in seen) if seen else "")


def _round_refs(inputs: dict) -> dict:
    """$roundN.key (what the loop is told to write) is the same as $stepN.key for the step runner."""
    import re

    return {k: (re.sub(r"^\$round(\d+)\.", r"$step\1.", v) if isinstance(v, str) else v) for k, v in inputs.items()}


def approval_resolved(store, approval_id: int, status: str, result: str) -> list[int]:
    """Called when a yes/no is given. Returns the loop tasks that were waiting only for this and can now carry on."""
    from prometheus.orchestrator.planner import paused_runs, save_paused_run

    ready = []
    for key, saved in paused_runs(store).items():
        st = saved.get("loop") if isinstance(saved, dict) else None
        if not st or approval_id not in (st.get("waiting") or []):
            continue
        st.setdefault("outcomes", {})[str(approval_id)] = {"status": status, "result": result}
        save_paused_run(store, int(key), saved)
        if all(str(a) in st["outcomes"] for a in st["waiting"]):
            ready.append(int(key))
    return ready
