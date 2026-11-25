"""The owner's own agents (M5 5.4): a name, a short description, instructions, ticked tools and a model card.

How it stays safe (all in code, none of it in a prompt):
- An agent starts with no tools. Only tools the owner ticked are ever offered to its model, and only the four that
  have a real, checked implementation here can be ticked (OWN_TOOLS).
- Every tool call the model asks for goes through `OwnAgent._call`: the tool must be on the agent's own list AND the
  shared permission table (`ctx.permissions`, the same one the built-in agents use) must say allow or ask. Anything
  else is refused, nothing runs, and the model is told it is not allowed.
- A tool that needs approval (opening a file) asks on the Approvals page every single time (never remembered).
- What a tool brings back (a web page, a file) goes to the model wrapped as untrusted data.
- After the agent has read one of the owner's files, the rest of its thinking uses the laptop's own AI only.
- Names are letters and underscores (so "@name" works), never the name of a built-in agent.
- Own agents are only in the app's own registry copy, never in the global one, and switch off with the Features switch
  "Own agents" (`agent_blocked` in features.py reads the saved list).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, untrusted
from prometheus.jobs.states import TERMINAL_STATES

OWN_TOOLS = ("web_search", "web_fetch", "read_files", "open_local")
WEB_TOOLS = frozenset({"web_search", "web_fetch"})          # refused once a run has read the owner's files
TOOL_HELP = {
    "web_search": "web_search(query): search the web; you get titles and links.",
    "web_fetch": "web_fetch(url): read one web page.",
    "read_files": "read_files(path): read one text file on the owner's laptop.",
    "open_local": "open_local(path): open a document, picture or video for the owner (they must say yes each time).",
}
MAX_AGENTS = 20
MAX_INSTRUCTIONS = 4000
MAX_ABOUT = 140
MAX_ROUNDS = 5
OBSERVATION_CHARS = 6000
FILE_BYTES = 40_000
MARKER = "[own agent]"
NAME = re.compile(r"^[a-z][a-z_]{1,29}$")
SCHEMA = {
    "type": "object",
    "properties": {
        "thought": {"type": "string"}, "done": {"type": "boolean"}, "answer": {"type": "string"},
        "tool": {"type": "string"},
        "args": {"type": "array", "items": {"type": "object", "properties": {
            "key": {"type": "string"}, "value": {"type": "string"}}, "required": ["key", "value"],
            "additionalProperties": False}},
    },
    "required": ["thought", "done", "answer", "tool", "args"],
    "additionalProperties": False,
}
APPROVAL_KIND = "own_open_local"


class OwnAgentError(ValueError):
    """The agent can't be saved; the message is in plain words for the owner."""


def _flat(name: str) -> str:
    return name.replace("_", "").lower()


# -- storage: the list lives in the saved settings (one source, written atomically by save_settings) ----------------
def specs(settings) -> list[dict]:
    return [dict(s) for s in (getattr(settings, "own_agent_specs", None) or []) if isinstance(s, dict)]


def clean(settings, spec: dict, builtin: set[str], cards: set[str], editing: str | None = None) -> dict:
    name = str(spec.get("name") or "").strip().lower()
    if not NAME.match(name):
        raise OwnAgentError("The name must be 2 to 30 letters or underscores (no spaces or numbers), so that "
                            "@name works.")
    if _flat(name) in {_flat(b) for b in builtin}:
        raise OwnAgentError(f"'{name}' is the name of a built-in agent. Choose another name.")
    others = [s["name"] for s in specs(settings) if s.get("name") != editing]
    if name in others or (editing is None and name in {s.get("name") for s in specs(settings)}):
        raise OwnAgentError(f"You already have an agent called '{name}'.")
    if editing is None and len(specs(settings)) >= MAX_AGENTS:
        raise OwnAgentError(f"You can have at most {MAX_AGENTS} own agents.")
    about = " ".join(str(spec.get("about") or "").split())[:MAX_ABOUT]
    instructions = str(spec.get("instructions") or "").strip()
    if not instructions:
        raise OwnAgentError("Write the instructions: what this agent should do and how.")
    if len(instructions) > MAX_INSTRUCTIONS:
        raise OwnAgentError(f"The instructions are too long (at most {MAX_INSTRUCTIONS} characters).")
    asked = spec.get("tools") or []
    asked = [asked] if isinstance(asked, str) else list(asked)
    bad = [t for t in asked if t not in OWN_TOOLS]
    if bad:
        raise OwnAgentError(f"Own agents can't use the tool '{bad[0]}'. They can use: {', '.join(OWN_TOOLS)}.")
    card = str(spec.get("card") or "").strip()
    if card and card not in cards:
        raise OwnAgentError("That model isn't one of the model cards. Choose Automatic or a listed model.")
    return {"name": name, "about": about or "My own agent", "instructions": instructions,
            "tools": list(dict.fromkeys(asked)), "card": card}


def add(settings, spec: dict, builtin: set[str], cards: set[str]) -> dict:
    new = clean(settings, spec, builtin, cards)
    settings.own_agent_specs = specs(settings) + [new]
    return new


def change(settings, name: str, spec: dict, builtin: set[str], cards: set[str]) -> dict:
    if name not in {s.get("name") for s in specs(settings)}:
        raise OwnAgentError(f"No own agent called '{name}'.")
    new = clean(settings, {**spec, "name": name}, builtin, cards, editing=name)
    settings.own_agent_specs = [new if s.get("name") == name else s for s in specs(settings)]
    return new


def delete(settings, name: str) -> None:
    settings.own_agent_specs = [s for s in specs(settings) if s.get("name") != name]


def valid_specs(settings, builtin: set[str]) -> list[dict]:
    """The saved agents that still pass every rule; a hand-edited file can only lose power, never gain it."""
    out: list[dict] = []
    for s in specs(settings):
        try:
            name = str(s.get("name") or "")
            if not NAME.match(name) or _flat(name) in {_flat(b) for b in builtin} or any(o["name"] == name for o in out):
                continue
            instructions = str(s.get("instructions") or "").strip()[:MAX_INSTRUCTIONS]
            if not instructions:
                continue
            out.append({"name": name, "about": " ".join(str(s.get("about") or "").split())[:MAX_ABOUT] or "My own agent",
                        "instructions": instructions,
                        "tools": [t for t in dict.fromkeys(s.get("tools") or []) if t in OWN_TOOLS],
                        "card": str(s.get("card") or "")})
        except Exception:                           # noqa: BLE001 - one bad entry never hides the rest
            continue
    return out[:MAX_AGENTS]


# -- the tools the model may ask for (patchable in tests) ---------------------------------------------------------------
def _search(query: str, n: int) -> list[dict]:
    from prometheus.agents.research import ddg_search

    return ddg_search(query, n)


def _fetch(url: str) -> str:
    import httpx

    from prometheus.safety.web import fetch_text

    with httpx.Client(headers={"User-Agent": "Prometheus/3.0 (personal assistant)"}) as http:
        return fetch_text(url, http)


def _open(path: str) -> None:
    from prometheus.agents.opener import system_open

    system_open(path)


SEARCH, FETCH, OPEN = _search, _fetch, _open


def open_handler(payload: dict, policy) -> str:
    """Runs only after the owner said yes on the Approvals page; the file is checked again first."""
    from prometheus.agents.opener import _checked_local

    path = _checked_local(str(payload.get("path", "")), policy)
    OPEN(str(path))
    return f"Opened {path.name}"


def _args(raw) -> dict:
    out = {}
    for item in raw or []:
        if isinstance(item, dict) and item.get("key"):
            out[str(item["key"])] = str(item.get("value", ""))
    return out


def _path(raw: str, ctx: AgentContext) -> Path:
    p = Path(raw).expanduser()
    return p if p.is_absolute() else ctx.home / p


# -- the agent ----------------------------------------------------------------------------------------------------------------
class OwnAgent(Agent):
    own = True
    accepts_private = True
    private_outputs = False

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        tools = [t for t in OWN_TOOLS if t in self.tools]
        help_lines = "\n".join(f"- {TOOL_HELP[t]}" for t in tools) or "You have no tools: answer from the task itself."
        system = (f'You are "{self.name}" {MARKER}, an agent the owner made.\n'
                  f"The owner's instructions for you:\n{self.instructions}\n\n{UNTRUSTED_RULE}\n"
                  "Never ask for a tool that isn't in this list; Prometheus refuses it.\n"
                  f"Your tools:\n{help_lines}\n\n"
                  'Reply with JSON only: {"thought": one short sentence, "done": true when you give the final answer, '
                  '"answer": the final answer when done else "", "tool": the tool to use now else "", '
                  '"args": [{"key": ..., "value": ...}]}. Use one tool at a time.')
        given = "\n".join(f"{k}: {untrusted(str(v)[:OBSERVATION_CHARS], 'step input')}" for k, v in (inputs or {}).items())
        messages = [{"role": "user", "content": f"Task: {goal}" + (f"\nGiven:\n{given}" if given else "")}]
        private = False
        for _ in range(MAX_ROUNDS):
            ctx.killswitch.check()
            with self._model(ctx):
                reply = ctx.router.complete("private" if private else "combine", messages, system, json_schema=SCHEMA,
                                            max_tokens=1500)
            try:
                data = json.loads(reply.text)
                if not isinstance(data, dict):
                    raise ValueError
            except ValueError:
                return StepResult("done", reply.text.strip())
            tool = str(data.get("tool") or "")
            if data.get("done") or not tool:
                return StepResult("done", str(data.get("answer") or data.get("thought") or "").strip())
            outcome = self._call(tool, _args(data.get("args")), ctx, read_a_file=private)
            if isinstance(outcome, StepResult):
                return outcome                                  # waiting for the owner's yes
            text, read_a_file = outcome
            private = private or read_a_file
            messages += [{"role": "assistant", "content": reply.text},
                         {"role": "user", "content": f"Result of {tool}:\n{text}"}]
        return StepResult("failed", f"{self.name} stopped after {MAX_ROUNDS} tries without an answer.")

    def _model(self, ctx: AgentContext):
        import contextlib

        use_card = getattr(ctx.router, "use_card", None)
        return use_card(self.card) if (self.card and use_card) else contextlib.nullcontext()

    def _call(self, tool: str, args: dict, ctx: AgentContext, read_a_file: bool = False):
        """The one door to every tool. Returns (text for the model, read_a_file) or a StepResult when waiting.
        Once this run has read one of the owner's files, nothing more goes to the web (a page could otherwise ask
        for a file and then for a link with its words in it): M5 review R8."""
        from prometheus.safety.permissions import Decision

        if tool not in OWN_TOOLS or tool not in self.tools or ctx.permissions.check(self.name, tool) is Decision.DENY:
            return f"The tool '{tool}' is not allowed for this agent. Nothing was done.", False
        if read_a_file and tool in WEB_TOOLS:
            return (f"The tool '{tool}' is not allowed after reading the owner's files in the same task (their words "
                    "never go to the web). Nothing was done.", False)
        try:
            if tool == "web_search":
                rows = SEARCH(args.get("query", ""), 5) or []
                body = "\n".join(f"{r.get('title', '')} - {r.get('url', '')}" for r in rows) or "No results."
                return untrusted(body[:OBSERVATION_CHARS], "web search"), False
            if tool == "web_fetch":
                from prometheus.safety.web import UnsafeURL, check_url

                url = args.get("url", "")
                try:
                    check_url(url)
                except (UnsafeURL, ValueError) as e:
                    return f"I can't open that address: {e}", False
                return untrusted(FETCH(url)[:OBSERVATION_CHARS], url), False
            if tool == "read_files":
                from prometheus.safety.paths import PathBlocked

                try:
                    path = ctx.policy.check_read(_path(args.get("path", ""), ctx))
                except PathBlocked as e:
                    return f"I can't read that: {e}", False
                if not path.is_file():
                    return "I can't read that: it isn't a file.", False
                raw = path.read_bytes()[:FILE_BYTES]
                if b"\x00" in raw:
                    return "I can't read that: it isn't a text file.", False
                return untrusted(raw.decode("utf-8", errors="replace")[:OBSERVATION_CHARS], path.name), True
            if tool == "open_local":
                from prometheus.agents.opener import _checked_local

                try:
                    path = _checked_local(str(_path(args.get("path", ""), ctx)), ctx.policy)
                except ValueError as e:
                    return f"I can't open that: {e}", False
                ctx.approvals.request(APPROVAL_KIND, f"{self.name} wants to open {path.name}", {"path": str(path)})
                return StepResult("waiting_approval", f"Opening {path.name} needs your yes on the Approvals page.")
        except Exception as e:                                  # noqa: BLE001 - a failing tool is a result, not a crash
            return f"The tool {tool} failed: {type(e).__name__}: {str(e)[:200]}", False
        return f"The tool '{tool}' is not allowed for this agent. Nothing was done.", False


def agent_class(spec: dict) -> type[OwnAgent]:
    tools = {t for t in spec.get("tools", []) if t in OWN_TOOLS}
    return type(f"OwnAgent_{spec['name']}", (OwnAgent,), {
        "name": spec["name"], "description": spec.get("about") or "My own agent", "tools": tools,
        "instructions": spec["instructions"], "card": spec.get("card", ""),
        "private_outputs": "read_files" in tools,                 # results hold the owner's file text
        "accepts_private": not ({"web_search", "web_fetch"} & tools)})   # never send private results to the web


def install(registry: dict, permissions, settings, builtin: set[str]) -> None:
    """Make the registry and the permission table match the saved agents (adds, updates, removes)."""
    for name in [n for n, c in registry.items() if getattr(c, "own", False)]:
        del registry[name]
        permissions.agent_tools.pop(name, None)
    for spec in valid_specs(settings, builtin):
        cls = agent_class(spec)
        registry[spec["name"]] = cls
        permissions.agent_tools[spec["name"]] = set(cls.tools)


# -- removing: its running jobs are cancelled first ----------------------------------------------------------------------------
def jobs_of(store, name: str) -> list:
    """Unfinished jobs that belong to this agent (steps titled "name: ...", or marked with its name)."""
    found = []
    for j in store.list_unfinished():
        if j.state in TERMINAL_STATES or j.payload.get("service"):
            continue
        if j.payload.get("agent") == name or j.title.startswith(f"{name}: "):
            found.append(j)
    return found


def stop_jobs(store, runner, graphs, jobs: list) -> int:
    """Cancel those jobs only (other agents' steps of the same graph carry on)."""
    from prometheus.jobs import limits
    from prometheus.jobs.states import CANCELLED

    stopped = 0
    for j in jobs:
        with store._lock:
            fresh = store.get_job(j.id)
            if fresh is None or fresh.state in TERMINAL_STATES:
                continue
            store.set_state(j.id, CANCELLED, error="The agent was removed")
        try:
            limits.end_job(j.id)
        except Exception:                                       # noqa: BLE001
            pass
        if runner is not None:
            task = runner.get_task(j.id)
            if task is not None and not task.done():
                task.cancel()
        stopped += 1
    return stopped
