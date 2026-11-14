"""What the new interface shows about Prometheus right now: online or not, what's running, and the `/` commands.

Everything here comes from real state. When nothing is running the panel says so; it never invents a task, an agent
or a progress number, and "online" is only claimed when Prometheus isn't paused and at least one AI can answer.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

# Commands typed after "/" (or picked from the Ctrl+/ palette). "fill" puts a starting prompt in the command bar,
# "go" opens a page, "do" runs an action in the page. Pages appear here only once they exist in the new interface.
COMMANDS: List[Dict[str, str]] = [
    {"command": "/code", "label": "Code", "group": "Ask", "kind": "fill", "value": "Help me with this code: ",
     "description": "Write, fix or explain code"},
    {"command": "/research", "label": "Research", "group": "Ask", "kind": "fill", "value": "Research ",
     "description": "Search the web and give sources"},
    {"command": "/write", "label": "Write", "group": "Ask", "kind": "fill", "value": "Write ",
     "description": "Draft an email, essay or message"},
    {"command": "/explain", "label": "Explain", "group": "Ask", "kind": "fill", "value": "Explain simply: ",
     "description": "Explain something in plain words"},
    {"command": "/plan", "label": "Plan", "group": "Ask", "kind": "fill", "value": "Make a plan for ",
     "description": "Break a goal into steps"},
    {"command": "/summarise", "label": "Summarise a file", "group": "Files", "kind": "fill",
     "value": "Summarise the file ", "description": "Summarise a document or attached file"},
    {"command": "/analyse", "label": "Analyse data", "group": "Files", "kind": "fill",
     "value": "Analyse this data: ", "description": "Look at a spreadsheet or CSV"},
    {"command": "/find", "label": "Find a file", "group": "Files", "kind": "fill",
     "value": "Find the file that ", "description": "Search inside your documents"},
    {"command": "/translate", "label": "Translate", "group": "Ask", "kind": "fill", "value": "Translate to English: ",
     "description": "Translate text, a file or a page"},
    {"command": "/remember", "label": "Remember", "group": "Memory", "kind": "fill", "value": "Remember ",
     "description": "Save something to memory at once"},
    {"command": "/remind", "label": "Remind me", "group": "Automate", "kind": "fill", "value": "Remind me ",
     "description": "A reminder, alarm or timer"},
    {"command": "/new", "label": "New chat", "group": "Go", "kind": "go", "value": "/",
     "description": "Start a fresh chat"},
    {"command": "/chats", "label": "Chats", "group": "Go", "kind": "go", "value": "/v3/chats",
     "description": "All your chats"},
    {"command": "/agents", "label": "Agents", "group": "Go", "kind": "go", "value": "/v3/agents",
     "description": "What each agent does, and switching them on or off"},
    {"command": "/tasks", "label": "Tasks", "group": "Go", "kind": "go", "value": "/v3/tasks",
     "description": "Everything you asked, with each step"},
    {"command": "/skills", "label": "Skills", "group": "Go", "kind": "go", "value": "/v3/skills",
     "description": "The guides Prometheus follows"},
    {"command": "/memory", "label": "Memory", "group": "Go", "kind": "go", "value": "/v3/memory",
     "description": "What it remembers about you"},
    {"command": "/study", "label": "Study", "group": "Go", "kind": "go", "value": "/v3/research?mode=study",
     "description": "Flashcards, exams and deadlines"},
    {"command": "/money", "label": "Money", "group": "Go", "kind": "go", "value": "/v3/research?mode=money",
     "description": "What you spent this month"},
    {"command": "/automations", "label": "Automations", "group": "Go", "kind": "go", "value": "/v3/automations",
     "description": "Reminders, scheduled goals and watchers"},
    {"command": "/files", "label": "Search my files", "group": "Go", "kind": "go", "value": "/v3/memory?tab=files",
     "description": "Search inside your files, and the files Prometheus made"},
    {"command": "/settings", "label": "Settings", "group": "Go", "kind": "go", "value": "/settings",
     "description": "Open Settings"},
    {"command": "/stop", "label": "Stop the reply", "group": "Do", "kind": "do", "value": "stop",
     "description": "Stop what Prometheus is doing in this chat"},
]


# Home quick actions: the first three are buttons, the rest are in "More". The owner can change them (Customise).
QUICK_ICONS = ("code", "research", "automations", "doc", "write", "chart", "bulb", "list", "folder-search", "memory",
               "chats", "image", "grid")
DEFAULT_QUICK: List[Dict[str, str]] = [
    {"label": "Code", "fill": "Help me with this code: ", "icon": "code"},
    {"label": "Research", "fill": "Research ", "icon": "research"},
    {"label": "Automate", "fill": "Every day at 8 am, ", "icon": "automations"},
    {"label": "Summarise a file", "fill": "Summarise the file ", "icon": "doc"},
    {"label": "Write", "fill": "Write ", "icon": "write"},
    {"label": "Analyse data", "fill": "Analyse this data: ", "icon": "chart"},
    {"label": "Explain", "fill": "Explain simply: ", "icon": "bulb"},
    {"label": "Plan", "fill": "Make a plan for ", "icon": "list"},
    {"label": "Search my files", "fill": "Find the file that ", "icon": "folder-search"},
]


def clean_quick(items) -> List[Dict[str, str]]:
    """The owner's quick actions, checked: at most 12, short labels and texts, known icons."""
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        label = str(it.get("label", "")).strip()[:24]
        fill = str(it.get("fill", ""))[:200]
        icon = it.get("icon") if it.get("icon") in QUICK_ICONS else "grid"
        if label and fill.strip():
            out.append({"label": label, "fill": fill, "icon": icon})
    return out[:12]


def quick_actions(settings) -> List[Dict[str, str]]:
    return clean_quick(getattr(settings, "quick_actions", None)) or [dict(q) for q in DEFAULT_QUICK]


class UIStateService:
    def __init__(self, services: Optional[Any] = None) -> None:
        self.services = services

    def get_slash_commands(self) -> List[Dict[str, str]]:
        out = [dict(c) for c in COMMANDS]
        skills = getattr(self.services, "skills", None) if self.services else None
        if skills is not None:
            try:
                for sk in skills.catalog()[:40]:
                    out.append({"command": "/" + sk.name.lower().replace(" ", "-"), "label": sk.name, "group": "Skills",
                                "kind": "fill", "value": f"Use the {sk.name} skill to ",
                                "description": (sk.description or "")[:90]})
            except Exception:
                pass
        return out

    def _running(self) -> tuple[Optional[int], Optional[dict]]:
        s = self.services
        if not s or not getattr(s, "running", None) or not getattr(s, "store", None):
            return None, None
        chat_id = next(iter(list(s.running)), None)
        if chat_id is None:
            return None, None
        try:
            return chat_id, s.store.latest_task(chat_id)
        except Exception:
            return chat_id, None

    def get_active_agent(self) -> Optional[Dict[str, Any]]:
        """The agent doing a step right now, or None when nothing is running."""
        _, task = self._running()
        if not task:
            return None
        try:
            steps = self.services.store.steps(task["id"])
        except Exception:
            return None
        running = [st for st in steps if st.status == "running"]
        if not running:
            return {"name": "Planning", "status": "working", "description": "Working out the steps"}
        st = running[-1]
        return {"name": st.agent.replace("_", " ").title(), "status": "working", "description": st.goal or ""}

    def get_current_task(self) -> Optional[Dict[str, Any]]:
        """The running request with real progress (finished steps out of planned ones), or None when idle."""
        chat_id, task = self._running()
        if chat_id is None:
            return None
        title = (task or {}).get("goal") or ""
        steps = []
        if task:
            try:
                steps = self.services.store.steps(task["id"])
            except Exception:
                steps = []
        done = sum(1 for st in steps if st.status in ("done", "failed", "skipped"))
        running = [st for st in steps if st.status == "running"]
        return {"chat_id": chat_id, "title": title[:120], "status": "running",
                "progress": round(done * 100.0 / len(steps), 1) if steps else None,
                "detail": (running[-1].goal if running else "Planning")[:160]}

    def get_online(self) -> Dict[str, Any]:
        s = self.services
        if s is None:
            return {"online": False, "status": "unknown", "label": "Starting"}
        if getattr(getattr(s, "killswitch", None), "is_set", False) or getattr(s.settings, "paused", False):
            return {"online": False, "status": "paused", "label": "Paused (Stop all)"}
        router = getattr(s, "router", None)
        try:
            usable = any(p.get("available") and not p.get("resting") for p in router.status_detail())
        except Exception:
            usable = False
        if not usable:
            return {"online": False, "status": "no_ai", "label": "No AI available"}
        return {"online": True, "status": "online", "label": "Prometheus online"}

    def get_full_state(self) -> Dict[str, Any]:
        online = self.get_online()
        return {"online": online["online"], "status": online["status"], "status_label": online["label"],
                "timestamp": time.time(), "active_agent": self.get_active_agent(),
                "current_task": self.get_current_task()}


ui_state_service = UIStateService()
