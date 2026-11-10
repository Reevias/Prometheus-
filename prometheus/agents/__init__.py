"""Agents. Importing this package registers every built-in agent."""
from prometheus.agents import (antigravity, documents, file_sorter, github, guide, opener, repo_scout,  # noqa: F401
                               research, system)
from prometheus.agents.base import REGISTRY  # noqa: F401
