"""Agents. Importing this package registers every built-in agent."""
from prometheus.agents import (antigravity, apps_agent, assignments_agent, briefing_agent, browser_agent, clipboard_agent, code_agent, calendar_agent, google_agent, documents, email_agent, exams_agent, file_sorter, focus_agent, files, github, guide, health_agent, homework_agent, jobs_agent, lecture_agent, money_agent, opener, reader_agent, repo_scout, screen_agent,  # noqa: F401
                               obsidian, phone_agent, photos_agent, reminders_agent, research, settings_agent, study_agent, system, tools_agent, translate_agent, undo_agent,
                               wikipedia)
from prometheus.agents.base import REGISTRY  # noqa: F401
