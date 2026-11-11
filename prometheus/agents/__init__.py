"""Agents. Importing this package registers every built-in agent."""
from prometheus.agents import (antigravity, apps_agent, briefing_agent, browser_agent, code_agent, calendar_agent, google_agent, documents, email_agent, file_sorter, files, github, guide, opener, repo_scout,  # noqa: F401
                               obsidian, photos_agent, reminders_agent, research, settings_agent, study_agent, system, tools_agent, undo_agent,
                               wikipedia)
from prometheus.agents.base import REGISTRY  # noqa: F401
