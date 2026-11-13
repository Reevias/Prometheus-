"""The Agents page (M2.2): every agent Prometheus has, with real status only.

Status comes from what is really true now: the owner's switch on this page, the Features switch it belongs to,
Stop everything, a step of this agent running right now, or an approval of its chat still waiting. Otherwise it
is Idle. "Offline" and "Error" are not guessed: the last run's real result is shown instead.
"""
from __future__ import annotations

import re

from prometheus.features import GROUPS, agent_on, feature_of, on, paused, SAFE_WHEN_PAUSED
from prometheus.safety.permissions import ALLOW_TOOLS, ASK_TOOLS

# what each tool lets an agent do, in plain words (capability tags and permissions)
TOOL_WORDS = {
    "read_files": "Reads your files", "move_files": "Moves and creates files", "web_search": "Searches the web",
    "web_fetch": "Opens web pages", "call_model": "Asks an AI", "github_search": "Searches GitHub",
    "github_read": "Reads GitHub", "github_write": "Writes to GitHub", "open_link": "Opens links",
    "open_local": "Opens files and apps", "open_other": "Opens other things", "system_read": "Reads laptop info",
    "system_change": "Changes laptop settings", "settings_read": "Reads Prometheus settings",
    "settings_write": "Changes Prometheus settings", "clone_repo": "Downloads repositories",
    "apply_patch": "Edits its own code", "generate_media": "Makes images", "cloud_agent": "Sends jobs to a cloud agent",
    "run_code": "Runs code in a sandbox",
}
STATUS_LABELS = {"running": "Running", "waiting": "Waiting for approval", "idle": "Idle", "off": "Off",
                 "feature_off": "Off in Features", "stopped": "Stopped (Stop all)"}


def short(description: str, limit: int = 140) -> str:
    """The first sentence of an agent's description (the rest is written for the planner)."""
    text = " ".join((description or "").split())
    first = re.split(r"(?<=[.!?])\s", text, maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def permission(tool: str) -> str:
    """Same rule as safety.permissions.Permissions.check: only the ALLOW tools run without asking."""
    return "allowed" if tool in ALLOW_TOOLS and tool not in ASK_TOOLS else "asks you first"


def running_agents(services) -> dict[str, dict]:
    """Agents with a step running right now: name -> {goal, chat_id}."""
    out: dict[str, dict] = {}
    for chat_id in list(getattr(services, "running", {}) or {}):
        task = services.store.latest_task(chat_id)
        if not task:
            continue
        for st in services.store.steps(task["id"]):
            if st.status == "running":
                out[st.agent] = {"goal": st.goal, "chat_id": chat_id}
    return out


def waiting_chats(services) -> set[int]:
    try:
        return {int(r["chat_id"]) for r in services.store._rows(
            "SELECT DISTINCT chat_id FROM approvals WHERE status = 'pending'")}
    except Exception:
        return set()


def status_of(services, name: str, activity: dict, running: dict, waiting: set[int]) -> str:
    s = services.settings
    if not agent_on(s, name):
        return "off"
    f = feature_of(name)
    if f is not None and not on(s, f.key):
        return "feature_off"
    if paused(s) and name not in SAFE_WHEN_PAUSED:
        return "stopped"
    if name in running:
        return "running"
    last = activity.get(name) or {}
    if last.get("status") == "waiting_approval" and last.get("chat_id") in waiting:
        return "waiting"
    return "idle"


def agent_card(services, name: str, cls, activity: dict, running: dict, waiting: set[int]) -> dict:
    f = feature_of(name)
    status = status_of(services, name, activity, running, waiting)
    last = activity.get(name) or {}
    tools = sorted(getattr(cls, "tools", set()) or set())
    return {"name": name, "title": name.replace("_", " ").title(), "about": short(cls.description),
            "description": " ".join((cls.description or "").split()), "status": status,
            "status_label": STATUS_LABELS[status], "group": f.group if f else "Core",
            "feature": f.label if f else "", "feature_key": f.key if f else "",
            "tags": [TOOL_WORDS.get(t, t.replace("_", " ")) for t in tools],
            "permissions": [{"tool": t, "what": TOOL_WORDS.get(t, t.replace("_", " ")), "rule": permission(t)}
                            for t in tools],
            "private": bool(getattr(cls, "private_outputs", False)), "on": agent_on(services.settings, name),
            "runs": int(last.get("runs") or 0), "last": last, "now": running.get(name)}


def all_agents(services, registry) -> list[dict]:
    activity = services.store.agent_activity()
    running = running_agents(services)
    waiting = waiting_chats(services)
    cards = [agent_card(services, n, c, activity, running, waiting) for n, c in registry.items()]
    order = {g: i for i, g in enumerate(("Core",) + GROUPS)}
    return sorted(cards, key=lambda c: (order.get(c["group"], 99), c["title"]))
