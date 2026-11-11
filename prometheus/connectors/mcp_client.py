"""A small client for MCP (Model Context Protocol) servers, the common plug-in format for AI tools.

Two kinds of server: a command run on this laptop (talks over stdin/stdout, e.g. `npx -y @notionhq/notion-mcp-server`)
or a web address (streamable HTTP). Only what Prometheus needs: start, list tools, call a tool.
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import threading

import httpx

PROTOCOL = "2025-06-18"
CLIENT = {"name": "prometheus", "version": "1.4"}
CALL_TIMEOUT = 120


class MCPError(Exception):
    pass


class StdioServer:
    """A server started as a local command. Its secrets go in environment variables, never in the command line."""

    def __init__(self, command: str, env: dict | None = None):
        argv = shlex.split(command, posix=os.name != "nt")
        if not argv:
            raise MCPError("The server needs a command.")
        try:
            self.proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         env={**os.environ, **(env or {})}, text=True, encoding="utf-8", bufsize=1)
        except OSError as e:
            raise MCPError(f"Couldn't start '{argv[0]}' ({e.strerror or e}). Is it installed?") from e
        self._id = 0
        self._lock = threading.Lock()

    def request(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT):
        with self._lock:
            self._id += 1
            mid = self._id
            self._send({"jsonrpc": "2.0", "id": mid, "method": method, "params": params or {}})
            result: dict = {}

            def read():
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        return
                    try:
                        msg = json.loads(line)
                    except ValueError:
                        continue
                    if msg.get("id") == mid:
                        result.update(msg)
                        return

            t = threading.Thread(target=read, daemon=True)
            t.start()
            t.join(timeout)
            if not result:
                raise MCPError(f"The server didn't answer '{method}' in time.")
            if "error" in result:
                raise MCPError(str(result["error"].get("message", result["error"]))[:300])
            return result.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        with self._lock:
            self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _send(self, msg: dict) -> None:
        if self.proc.poll() is not None:
            raise MCPError("The server has stopped.")
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        try:
            self.proc.terminate()
        except Exception:
            pass


class HttpServer:
    """A server at a web address (streamable HTTP). A token, if any, is sent as a Bearer header."""

    def __init__(self, url: str, token: str = "", http: httpx.Client | None = None):
        if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
            raise MCPError("Use an https:// address (or a local http://127.0.0.1 one).")
        self.url, self.token = url, token
        self.http = http or httpx.Client(timeout=CALL_TIMEOUT)
        self.session = ""
        self._id = 0

    def _post(self, body: dict):
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
                   "MCP-Protocol-Version": PROTOCOL}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        try:
            resp = self.http.post(self.url, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise MCPError(f"Couldn't reach the server ({type(e).__name__}).") from e
        if resp.status_code in (401, 403):
            raise MCPError("The server refused the token.")
        if resp.status_code >= 400:
            raise MCPError(f"The server answered with error {resp.status_code}.")
        self.session = resp.headers.get("Mcp-Session-Id", self.session)
        return resp

    def request(self, method: str, params: dict | None = None, timeout: float = CALL_TIMEOUT):
        self._id += 1
        resp = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params or {}})
        text = resp.text
        if "text/event-stream" in resp.headers.get("content-type", ""):
            msgs = [json.loads(l[5:]) for l in text.splitlines() if l.startswith("data:") and l[5:].strip()]
            msg = next((m for m in msgs if m.get("id") == self._id), msgs[-1] if msgs else {})
        else:
            msg = json.loads(text or "{}")
        if "error" in msg:
            raise MCPError(str(msg["error"].get("message", msg["error"]))[:300])
        return msg.get("result", {})

    def notify(self, method: str, params: dict | None = None) -> None:
        try:
            self._post({"jsonrpc": "2.0", "method": method, "params": params or {}})
        except MCPError:
            pass

    def close(self) -> None:
        pass


class Connection:
    """One connected server: started once, its tools listed, then called by name."""

    def __init__(self, name: str, server):
        self.name, self.server = name, server
        self.tools: list[dict] = []

    def start(self) -> list[dict]:
        self.server.request("initialize", {"protocolVersion": PROTOCOL, "capabilities": {}, "clientInfo": CLIENT})
        self.server.notify("notifications/initialized")
        tools, cursor = [], None
        for _ in range(10):
            page = self.server.request("tools/list", {"cursor": cursor} if cursor else {})
            tools += page.get("tools", [])
            cursor = page.get("nextCursor")
            if not cursor:
                break
        self.tools = tools
        return tools

    def read_only(self, tool: str) -> bool:
        t = next((t for t in self.tools if t.get("name") == tool), {})
        return bool((t.get("annotations") or {}).get("readOnlyHint"))

    def call(self, tool: str, arguments: dict) -> str:
        if not any(t.get("name") == tool for t in self.tools):
            raise MCPError(f"{self.name} has no tool called {tool}.")
        result = self.server.request("tools/call", {"name": tool, "arguments": arguments or {}})
        parts = []
        for item in result.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "resource":
                parts.append(str((item.get("resource") or {}).get("text", ""))[:4000])
            else:
                parts.append(f"[{item.get('type')} content]")
        if result.get("structuredContent") and not parts:
            parts.append(json.dumps(result["structuredContent"])[:8000])
        text = "\n".join(parts)[:20_000]
        if result.get("isError"):
            raise MCPError(text[:500] or "The tool reported an error.")
        return text
