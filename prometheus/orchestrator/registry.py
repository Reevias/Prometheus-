"""The tool and agent list (M3.2): what each agent needs, what it may touch, and which device it runs on.

Every agent gets a spec: its inputs (the ones it must always get), its permission scope (the tool groups it may use,
checked again by the permission system at run time), whether its results hold private data, and where it runs:

- laptop: needs this laptop (its screen, files, apps or browser). Asked from the phone or the cloud copy, the request
  waits for the laptop and runs when it is back (owner: queue it and say so).
- phone: needs the phone (phone control). Asked while the phone is away, the request waits for the phone.
- both: works wherever Prometheus runs.
"""
from __future__ import annotations

from dataclasses import dataclass

from prometheus.phone import LAPTOP_AGENTS

PHONE_AGENTS = frozenset({"phone"})
RUNS_ON_LABEL = {"laptop": "laptop only", "phone": "needs the phone", "both": ""}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    required_inputs: tuple[str, ...]
    scope: tuple[str, ...]
    runs_on: str
    private_outputs: bool
    accepts_private: bool


def runs_on(name: str) -> str:
    if name in PHONE_AGENTS:
        return "phone"
    if name in LAPTOP_AGENTS:
        return "laptop"
    return "both"


def spec(name: str, agent_cls) -> ToolSpec:
    return ToolSpec(name=name, description=str(getattr(agent_cls, "description", "")),
                    required_inputs=tuple(getattr(agent_cls, "required_inputs", ())),
                    scope=tuple(sorted(getattr(agent_cls, "tools", set()) or ())),
                    runs_on=runs_on(name), private_outputs=bool(getattr(agent_cls, "private_outputs", False)),
                    accepts_private=bool(getattr(agent_cls, "accepts_private", True)))


def specs(registry: dict) -> list[ToolSpec]:
    return [spec(n, c) for n, c in registry.items()]


def planner_line(name: str, agent_cls) -> str:
    """One line for the planner's agent list: what it does, what it must be given, and where it runs."""
    s = spec(name, agent_cls)
    extra = []
    if s.required_inputs:
        extra.append("needs: " + ", ".join(s.required_inputs))
    if RUNS_ON_LABEL[s.runs_on]:
        extra.append(RUNS_ON_LABEL[s.runs_on])
    return f"- {name}: {s.description}" + (f" [{'; '.join(extra)}]" if extra else "")


def needs_laptop(steps) -> bool:
    return any(runs_on(st["agent"]) == "laptop" for st in steps)
