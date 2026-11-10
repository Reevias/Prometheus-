"""Agents. Importing this package registers every built-in agent."""
from prometheus.agents import (antigravity, documents, file_sorter, guide, opener, repo_scout, research,  # noqa: F401
                               system)
from prometheus.agents.base import REGISTRY  # noqa: F401
