"""UI State Provider and Slash Command Registry for Prometheus 3.0."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional


class UIStateService:
    """Provides current UI state, active agent, task progress, and slash command registry."""

    def __init__(self, services: Optional[Any] = None) -> None:
        self.services = services

    def get_slash_commands(self) -> List[Dict[str, str]]:
        """Return registered slash commands."""
        return [
            {
                "command": "/code",
                "label": "Code",
                "description": "Code assistant for editing, refactoring, and debugging",
                "example": "/code refactor safety checks",
            },
            {
                "command": "/research",
                "label": "Research",
                "description": "Deep academic and technical web research",
                "example": "/research latest advances in agent architectures",
            },
            {
                "command": "/automate",
                "label": "Automate",
                "description": "Schedule or run recurring background automations",
                "example": "/automate check disk space every morning",
            },
            {
                "command": "/pc",
                "label": "Control PC",
                "description": "Desktop integration and system control tasks",
                "example": "/pc open terminal in workspace",
            },
            {
                "command": "/agents",
                "label": "Agents",
                "description": "Inspect registered agents and active workers",
                "example": "/agents list",
            },
            {
                "command": "/memory",
                "label": "Memory",
                "description": "Search long-term memory, facts, and conversation context",
                "example": "/memory recent decisions",
            },
            {
                "command": "/skills",
                "label": "Skills",
                "description": "View and manage available tools and skills",
                "example": "/skills list",
            },
            {
                "command": "/system",
                "label": "System",
                "description": "System health, telemetry metrics, and resource monitors",
                "example": "/system status",
            },
            {
                "command": "/help",
                "label": "Help",
                "description": "Show command guide and keyboard shortcuts",
                "example": "/help",
            },
        ]

    def get_active_agent(self) -> Dict[str, Any]:
        """Return information about the currently active or standby agent."""
        if self.services and getattr(self.services, "running", None):
            # Check if any chat currently has a running task
            running_chats = list(self.services.running)
            if running_chats and getattr(self.services, "store", None):
                task = self.services.store.latest_task(running_chats[0])
                if task:
                    steps = [s for s in self.services.store.steps(task["id"]) if s.status == "running"]
                    if steps:
                        curr = steps[-1]
                        return {
                            "name": curr.agent.replace("_", " ").title(),
                            "role": "Active Execution Worker",
                            "status": "working",
                            "status_text": "Working",
                            "description": curr.goal or "Executing requested instructions...",
                        }

        # Standby / Core Orchestrator
        return {
            "name": "System Orchestrator",
            "role": "Core Intelligence",
            "status": "idle",
            "status_text": "Ready",
            "description": "Ready to assist with development and system tasks",
        }

    def get_current_task(self) -> Dict[str, Any]:
        """Return information about current task progress."""
        if self.services and getattr(self.services, "running", None):
            running_chats = list(self.services.running)
            if running_chats and getattr(self.services, "store", None):
                task = self.services.store.latest_task(running_chats[0])
                if task:
                    steps = self.services.store.steps(task["id"])
                    if steps:
                        completed = sum(1 for s in steps if s.status == "done")
                        total = max(len(steps), 1)
                        progress = round((completed / total) * 100.0, 1)
                        running_steps = [s for s in steps if s.status == "running"]
                        step_goal = running_steps[-1].goal if running_steps else "Processing..."
                        return {
                            "title": task.get("goal") or "Executing Task",
                            "status": "running",
                            "progress": progress,
                            "detail": step_goal,
                        }

        return {
            "title": "System Monitoring",
            "status": "ready",
            "progress": 100.0,
            "detail": "All systems operational",
        }

    def get_full_state(self) -> Dict[str, Any]:
        """Return unified state snapshot for the v3 interface."""
        return {
            "online": True,
            "timestamp": time.time(),
            "active_agent": self.get_active_agent(),
            "current_task": self.get_current_task(),
            "slash_commands": self.get_slash_commands(),
        }


# Global singleton helper
ui_state_service = UIStateService()
