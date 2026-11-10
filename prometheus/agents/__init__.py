"""Agents. Importing this package registers every built-in agent."""
from prometheus.agents import (antigravity, documents, file_sorter, files, github, guide, opener, repo_scout,  # noqa: F401
                               obsidian, research, settings_agent, system,
                               wikipedia)
from prometheus.agents.base import REGISTRY  # noqa: F401
