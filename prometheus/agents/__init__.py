"""Agents. Importing this package registers every built-in agent."""
from prometheus.agents import documents, file_sorter, opener, repo_scout, research  # noqa: F401
from prometheus.agents.base import REGISTRY  # noqa: F401
