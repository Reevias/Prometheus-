"""Agents. Importing this package registers every built-in agent."""
from prometheus.agents import (antigravity, apps_agent, calendar_agent, documents, email_agent, file_sorter, files, github, guide, opener, repo_scout,  # noqa: F401
                               obsidian, reminders_agent, research, settings_agent, system,
                               wikipedia)
from prometheus.agents.base import REGISTRY  # noqa: F401
