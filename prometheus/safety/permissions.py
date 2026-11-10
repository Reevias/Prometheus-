"""Which agent may use which tool, and which tools need the user's yes."""
from __future__ import annotations

from enum import Enum

ALLOW_TOOLS = {"read_files", "move_files", "web_search", "web_fetch", "call_model", "github_search", "open_link"}
ASK_TOOLS = {"clone_repo", "apply_patch", "generate_media", "open_other", "open_local"}


class Decision(Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class Permissions:
    def __init__(self, agent_tools: dict[str, set[str]]):
        self.agent_tools = {k: set(v) for k, v in agent_tools.items()}

    def check(self, agent: str, tool: str) -> Decision:
        if tool not in self.agent_tools.get(agent, set()):
            return Decision.DENY
        if tool in ASK_TOOLS:
            return Decision.ASK
        if tool in ALLOW_TOOLS:
            return Decision.ALLOW
        return Decision.ASK
