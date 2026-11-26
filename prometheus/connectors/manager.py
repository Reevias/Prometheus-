"""Keeps the connected MCP servers running and tells the planner what they can do."""
from __future__ import annotations

import re
import threading

from prometheus.config import get_secret
from prometheus.connectors.mcp_client import Connection, HttpServer, MCPError, StdioServer

NAME = re.compile(r"^[A-Za-z][A-Za-z0-9 _-]{0,30}$")
MOBILE_MCP_NAME = "mobile-mcp"
# pinned to the version checked in 6.0 (licence and code): -y with no version would run whatever is newest
MOBILE_MCP_TARGET = "npx -y @mobilenext/mobile-mcp@1.0.20 --stdio"


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


def ensure_mobile_mcp(settings, *, target: str = MOBILE_MCP_TARGET, enable: bool = True) -> dict:
    """Register or update mobile-mcp as a separate MCP server under Settings -> Other apps."""
    servers = list(getattr(settings, "mcp_servers", None) or [])
    existing = next((e for e in servers if isinstance(e, dict) and e.get("name", "").lower() == MOBILE_MCP_NAME), None)
    if existing is not None:
        if not existing.get("target"):
            existing["target"] = target
        existing["kind"] = existing.get("kind") or "command"
        existing["enabled"] = bool(enable)
        settings.mcp_servers = servers
        return existing
    entry = {"name": MOBILE_MCP_NAME, "kind": "command", "target": target, "enabled": bool(enable)}
    servers.append(entry)
    settings.mcp_servers = servers
    return entry


def find_mobile_mcp(manager: ConnectionManager) -> Connection:
    """Connect to the mobile-mcp server registered in Other apps (by name or matching mobile toolset)."""
    if manager is None:
        raise MCPError("Connections aren't set up.")
    entry = manager.entry(MOBILE_MCP_NAME)
    if entry is not None and entry.get("enabled"):
        return manager.get(entry["name"])
    for e in manager.entries():
        if not e.get("enabled"):
            continue
        if "mobile-mcp" in str(e.get("target", "")).lower():
            return manager.get(e["name"])
    raise MCPError("Phone control uses mobile-mcp in Other apps. Connect mobile-mcp in Settings → Connections.")
