"""Keeps the connected MCP servers running and tells the planner what they can do."""
from __future__ import annotations

import re
import threading

from prometheus.config import get_secret
from prometheus.connectors.mcp_client import Connection, HttpServer, MCPError, StdioServer

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,30}$")


RESERVED = {"MCP_SERVER_TOKEN"}          # Prometheus's own key for apps that call IT


def secret_name(server: str) -> str:
    name = "MCP_" + re.sub(r"[^A-Z0-9]+", "_", server.upper()).strip("_")[:40]
    return name + "_APP" if name in RESERVED else name


def parse_env(text: str) -> dict:
    """'NOTION_TOKEN=abc;OTHER=x' → dict (for servers started as a command)."""
    out = {}
    for part in (text or "").split(";"):
        key, sep, value = part.partition("=")
        if sep and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,60}", key.strip()):
            out[key.strip()] = value.strip()
    return out


class ConnectionManager:
    def __init__(self, settings, factory=None):
        self.settings = settings
        self.factory = factory           # tests: name, entry -> server object
        self.live: dict[str, Connection] = {}
        self.errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def entries(self) -> list[dict]:
        return [e for e in (self.settings.mcp_servers or []) if isinstance(e, dict) and NAME.fullmatch(e.get("name", ""))]

    def entry(self, name: str) -> dict | None:
        return next((e for e in self.entries() if e["name"].lower() == (name or "").lower()), None)

    def _server(self, entry: dict):
        if self.factory is not None:
            return self.factory(entry)
        secret = get_secret(secret_name(entry["name"])) or ""
        if entry.get("kind") == "url":
            return HttpServer(entry["target"], token=secret)
        return StdioServer(entry["target"], env=parse_env(secret))

    def get(self, name: str) -> Connection:
        entry = self.entry(name)
        if entry is None or not entry.get("enabled"):
            raise MCPError(f"No connected app called {name}. Connect it in Settings, under Connections.")
        with self._lock:
            conn = self.live.get(entry["name"])
            if conn is None:
                try:
                    conn = Connection(entry["name"], self._server(entry))
                    conn.start()
                except MCPError as e:
                    self.errors[entry["name"]] = str(e)
                    raise
                self.live[entry["name"]] = conn
                self.errors.pop(entry["name"], None)
            return conn

    def drop(self, name: str) -> None:
        with self._lock:
            conn = self.live.pop(name, None)
        if conn is not None:
            conn.server.close()

    def catalog_text(self, limit: int = 60) -> str:
        """What the planner sees: each connected app and its tools (started on first need)."""
        lines = []
        for e in self.entries():
            if not e.get("enabled"):
                continue
            try:
                tools = self.get(e["name"]).tools
            except MCPError:
                continue
            for t in tools[:limit]:
                desc = " ".join(str(t.get("description") or "").split())[:140]
                ro = " (reads only)" if (t.get("annotations") or {}).get("readOnlyHint") else ""
                lines.append(f"- {e['name']} / {t.get('name')}{ro}: {desc}")
        return "\n".join(lines)
