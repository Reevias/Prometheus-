"""Builds every service once and wires them together."""
from __future__ import annotations

import dataclasses
import threading
from dataclasses import dataclass
from pathlib import Path

import prometheus
from prometheus.actions.files import FileActions
from prometheus.actions.journal import Journal
from prometheus.agents import REGISTRY
from prometheus.agents.base import AgentContext
from prometheus.agents.opener import open_link_handler, open_local_handler
from prometheus.agents.repo_scout import clone_handler
from prometheus.config import Settings, data_dir
from prometheus.memory.store import Store
from prometheus.models.base import Provider
from prometheus.models.router import ModelRouter, build_router
from prometheus.orchestrator.planner import Orchestrator, TaskOutcome
from prometheus.safety.approvals import Approvals
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch
from prometheus.safety.paths import PathPolicy
from prometheus.safety.permissions import Permissions
from prometheus.selfedit.patcher import Patcher
from prometheus.selfedit.prompts import PromptStore

PACKAGE_DIR = Path(prometheus.__file__).resolve().parent
CODE_DIR = PACKAGE_DIR.parent


@dataclass
class Services:
    settings: Settings
    home: Path
    store: Store
    policy: PathPolicy
    journal: Journal
    files: FileActions
    permissions: Permissions
    approvals: Approvals
    budget: Budget
    killswitch: KillSwitch
    router: ModelRouter
    orchestrator: Orchestrator
    patcher: Patcher
    prompts: PromptStore
    running: dict = dataclasses.field(default_factory=dict)

    def start_task(self, chat_id: int, text: str) -> threading.Thread:
        """Run a task in the background so the browser can keep polling."""
        def work():
            try:
                self.running[chat_id] = True
                self.orchestrator.run(chat_id, text)
            finally:
                self.running.pop(chat_id, None)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread


def apply_settings(services: "Services") -> None:
    """Make changed model names and the budget cap take effect without a restart."""
    s, router = services.settings, services.router
    services.budget.cap_usd = s.budget_usd
    for name, model in (("openrouter", s.openrouter_model), ("anthropic", s.anthropic_model),
                        ("ollama", s.ollama_model)):
        provider = router.providers.get(name)
        if provider is not None and hasattr(provider, "model") and model:
            provider.model = model
    research = [e for e in s.routes.get("research", [])]
    if s.openrouter_fast_model:
        research = [f"openrouter:{s.openrouter_fast_model}" if e == "openrouter" else e for e in research]
    if "research" in router.routes and any(e.split(":")[0] in router.providers for e in research):
        router.routes["research"] = research


def build_services(settings: Settings, providers: dict[str, Provider] | None = None,
                   home: Path | None = None) -> Services:
    data = data_dir()
    home = Path(home or Path.home())
    store = Store(data / "memory.db")
    store.mark_interrupted()
    policy = PathPolicy(home=home, data_dir=data, code_dir=CODE_DIR)
    journal = Journal(data / "journals")
    files = FileActions(policy, journal)
    permissions = Permissions({name: cls.tools for name, cls in REGISTRY.items()})
    approvals = Approvals(store)
    budget = Budget(store, settings.budget_usd)
    killswitch = KillSwitch()
    if providers is None:
        router = build_router(settings, budget, killswitch)
    else:
        router = ModelRouter(providers, settings.routes, budget, killswitch)
    prompts = PromptStore(data / "prompts")
    patcher = Patcher(PACKAGE_DIR, approvals, data / "backups")
    approvals.register("apply_patch", patcher.apply)
    approvals.register("clone_repo", lambda payload: clone_handler(payload, data / "quarantine"))
    approvals.register("open_link", open_link_handler)
    approvals.register("open_local", lambda payload: open_local_handler(payload, policy))

    def make_context(task_id: int, step_id: int) -> AgentContext:
        return AgentContext(task_id=task_id, step_id=step_id, store=store, router=router, policy=policy, files=files,
                            permissions=permissions, approvals=approvals, killswitch=killswitch, home=home)

    orchestrator = Orchestrator(store, router, REGISTRY, make_context, killswitch, prompts.folder)
    return Services(settings, home, store, policy, journal, files, permissions, approvals, budget, killswitch,
                    router, orchestrator, patcher, prompts)


__all__ = ["Services", "build_services", "TaskOutcome"]
