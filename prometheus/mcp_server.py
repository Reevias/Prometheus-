"""Prometheus as a tool for other AI apps (Antigravity, Claude Desktop, Cursor…), over MCP.

Two ways in, both answered by the running Prometheus:
- web address: POST http://127.0.0.1:<port>/api/mcp with "Authorization: Bearer <token>" (streamable HTTP, JSON);
- command: `python -m prometheus.cli mcp` (stdio), which forwards each message to that address with the token.

What other apps may do (the owner's choice): read and ask; anything that changes something waits on the Approvals
page. Text from the owner's private files never leaves through here: file search gives names and places only, and
answers made from private files are replaced by a note.
"""
from __future__ import annotations

import json
import secrets
import sys

from prometheus.orchestrator.planner import PRIVATE_PLACEHOLDER

PROTOCOL = "2025-06-18"
SERVER_INFO = {"name": "prometheus", "version": "1"}
CHAT = "Other apps"
TOKEN_KEY = "MCP_SERVER_TOKEN"
ANSWER_WAIT = 240

TOOLS = [
    {"name": "search_memory", "description": "Search what Prometheus remembers about its owner: saved memories, "
     "chat notes and known facts about people, places and things.",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "facts_about", "description": "Everything Prometheus knows about one person or thing (or 'me' for the "
     "owner), with past values and when they changed.",
     "inputSchema": {"type": "object", "properties": {"subject": {"type": "string"}}, "required": ["subject"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "find_files", "description": "Find the owner's files by what is written in them. Returns file names and "
     "folders only (the text stays private on the laptop).",
     "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "ask", "description": "Ask Prometheus a question, as if the owner typed it. Questions only: requests "
     "to change things are turned down here; use request_action.",
     "inputSchema": {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]},
     "annotations": {"readOnlyHint": True}},
    {"name": "request_action", "description": "Ask Prometheus to DO something (move files, add a reminder, open an "
     "app…). It waits on the owner's Approvals page and runs only after their yes.",
     "inputSchema": {"type": "object", "properties": {"task": {"type": "string"},
                                                      "why": {"type": "string"}}, "required": ["task"]}},
]


def token(create: bool = True) -> str:
    from prometheus.config import get_secret, set_secret

    t = get_secret(TOKEN_KEY) or ""
    if not t and create:
        t = secrets.token_urlsafe(32)
        set_secret(TOKEN_KEY, t)
    return t


def _text(result: str, error: bool = False) -> dict:
    return {"content": [{"type": "text", "text": result}], "isError": error}


class PrometheusMCP:
    def __init__(self, services, caller: str = "another app"):
        self.services, self.caller = services, caller

    def chat(self) -> int:
        store = self.services.store
        for chat_id, title in store.chats():
            if title == CHAT:
                return chat_id
        chat_id = store.create_chat(CHAT)
        store.rename_chat(chat_id, CHAT, by="user")
        return chat_id

    # -- the tools -------------------------------------------------------------------------
    def search_memory(self, query: str) -> str:
        store = self.services.store
        hits = [h.text for h in store.recall(query, limit=10, kinds=("memory", "chat_note"))]
        facts = self.services.orchestrator.facts.relevant(query, limit=15)
        lines = [f"- {h[:400]}" for h in hits] + [f"- {f}" for f in facts]
        return "\n".join(dict.fromkeys(lines)) or "Nothing remembered about that."

    def facts_about(self, subject: str) -> str:
        from prometheus.memory.facts import clean_subject

        facts = self.services.orchestrator.facts.about(clean_subject(subject))
        if not facts:
            return f"No facts about {subject}."
        return "\n".join(f"- {f.attribute}: {f.value}" + (" (past)" if f.until else "") for f in facts)

    def find_files(self, query: str) -> str:
        index = self.services.file_index
        if index is None:
            return "File search isn't set up."
        hits = index.search(query, limit=10)
        return "\n".join(f"- {h['name']} ({h['path']})" for h in hits) or "No file mentions that."

    def ask(self, question: str) -> str:
        from prometheus.quick import is_question, understand

        fast = understand(question, self.services.store)
        if fast is not None and fast.action or (fast is None and not is_question(question)
                                                and _sounds_like_action(question)):
            return "That would change something, so it needs the owner: use request_action instead."
        chat = self.chat()
        store = self.services.store
        before = len(store.messages(chat))
        thread = self.services.start_task(chat, f"[asked by {self.caller}] {question}", read_only=True)
        thread.join(timeout=ANSWER_WAIT)
        if thread.is_alive():
            return "Prometheus is still working on it; the answer will be in its 'Other apps' chat."
        new = [m for m in store.messages(chat)[before:] if m.role == "assistant"]
        if not new:
            return "No answer."
        answer = new[-1]
        if answer.private:
            return PRIVATE_PLACEHOLDER + " The owner can read it in Prometheus."
        return answer.text

    def request_action(self, task: str, why: str = "") -> str:
        summary = f"{self.caller} asks Prometheus to: {task[:300]}" + (f" (why: {why[:200]})" if why else "")
        self.services.approvals.request("mcp_action", summary, {"task": task[:2000], "caller": self.caller})
        self.services.notify("waiting", f"{self.caller} asks: {task[:80]}")
        return "Sent to the owner's Approvals page. It runs only after they approve it."

    # -- the protocol ------------------------------------------------------------------------
    def handle(self, msg: dict) -> dict | None:
        mid, method, params = msg.get("id"), msg.get("method", ""), msg.get("params") or {}
        if mid is None:                      # a notification: nothing to answer
            return None
        try:
            if method == "initialize":
                info = params.get("clientInfo") or {}
                if info.get("name"):
                    self.caller = str(info["name"])[:60]
                result = {"protocolVersion": PROTOCOL, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO,
                          "instructions": "Prometheus is its owner's personal assistant on their laptop. Read and "
                                          "ask freely; anything that changes something needs request_action."}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": TOOLS}
            elif method == "tools/call":
                name, args = params.get("name"), params.get("arguments") or {}
                fn = {"search_memory": self.search_memory, "facts_about": self.facts_about,
                      "find_files": self.find_files, "ask": self.ask, "request_action": self.request_action}.get(name)
                if fn is None:
                    return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32602, "message": f"Unknown tool {name}"}}
                try:
                    result = _text(fn(**{k: str(v) for k, v in args.items() if k in fn.__code__.co_varnames}))
                except TypeError as e:
                    result = _text(f"Missing input: {e}", error=True)
                self.services.store.audit("mcp", name, {"caller": self.caller})
            else:
                return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": f"No method {method}"}}
        except Exception as e:
            return {"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": type(e).__name__}}
        return {"jsonrpc": "2.0", "id": mid, "result": result}


def _sounds_like_action(text: str) -> bool:
    import re

    return bool(re.match(r"^\s*(please\s+)?(open|close|delete|remove|move|copy|rename|send|post|install|turn|set|"
                         r"add|create|make|write|run|start|stop|book|buy|pay|shut|restart|play)\b", text, re.I))


def action_handler(services):
    def run(payload: dict) -> str:
        chat = PrometheusMCP(services).chat()
        services.start_task(chat, str(payload.get("task", "")))
        return "Approved: Prometheus is doing it now (see the 'Other apps' chat)."
    return run


def stdio_bridge(port: int, stdin=None, stdout=None, http=None) -> int:
    """`prometheus mcp`: each line from the other app goes to the running Prometheus; its reply comes back."""
    import httpx

    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    http = http or httpx.Client(timeout=ANSWER_WAIT + 30)
    headers = {"Authorization": f"Bearer {token()}", "Content-Type": "application/json"}
    url = f"http://127.0.0.1:{port}/api/mcp"
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        try:
            resp = http.post(url, content=line, headers=headers)
            reply = resp.json() if resp.status_code == 200 and resp.content else None
        except Exception:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {
                "code": -32000, "message": "Prometheus isn't running. Open it first."}} if "id" in msg else None
        if reply is not None:
            stdout.write(json.dumps(reply) + "\n")
            stdout.flush()
    return 0
