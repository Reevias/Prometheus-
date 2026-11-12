"""Builds every service once and wires them together."""
from __future__ import annotations

import dataclasses
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import prometheus
from prometheus.actions.files import FileActions
from prometheus.actions.journal import Journal
from prometheus.agents import REGISTRY
from prometheus.agents.base import AgentContext
from prometheus.agents.antigravity import AntigravityRunner
from prometheus.agents.github import GitHubAgent, github_action_handler
from prometheus.agents.opener import open_link_handler, open_local_handler
from prometheus.agents.system import laptop_action_handler, wifi_password_handler
from prometheus.laptop import Laptop, sweep_wifi_profiles
from prometheus.agents.repo_scout import clone_handler
from prometheus.config import Settings, data_dir, save_settings
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
from prometheus.skillbook import SkillBook

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
    skills: SkillBook = None
    running: dict = dataclasses.field(default_factory=dict)
    archive: object = None
    voice: object = None
    updater: object = None
    daily: object = None
    reminders: object = None
    watchers: object = None
    briefing: object = None
    file_index: object = None
    clips: object = None
    sync: object = None
    laptop_jobs: object = None
    resumer: object = None
    focus: object = None
    photos: object = None
    laptop: object = None
    browser: object = None
    notifier: object = None          # shows a Windows notification (None when not on Windows)

    def notify(self, kind: str, text: str) -> bool:
        """A Windows notification, if the owner wants this kind (Settings → Notifications)."""
        if self.focus is not None and kind != "reminder" and self.focus.hold(text):
            return False                       # focus mode: kept for the summary at the end
        wanted = {"task": "notify_tasks", "waiting": "notify_waiting", "briefing": "notify_briefing",
                  "repo": "notify_repo", "update": "notify_updates"}.get(kind)
        if wanted and not getattr(self.settings, wanted, True):
            return False
        if self.notifier is None:
            return False
        try:
            self.notifier(text)
            return True
        except Exception:
            return False

    def start_rewrite(self, chat_id: int, message_id: int, note: str = "") -> threading.Thread:
        """Rewrite an answer the owner marked 'could be better', in the background."""
        from prometheus.learning import rewrite

        def work():
            try:
                self.running[chat_id] = True
                rewrite(self, message_id, note)
            except Exception as e:      # no model right now: say so in the chat
                self.store.add_message(chat_id, "assistant", f"I couldn't rewrite that just now ({e}).", "rewrite")
            finally:
                self.running.pop(chat_id, None)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread

    def start_task(self, chat_id: int, text: str, read_only: bool = False) -> threading.Thread:
        """Run a task in the background so the browser can keep polling. read_only: only reading steps run."""
        def work():
            started, outcome = time.monotonic(), None
            try:
                self.running[chat_id] = True
                outcome = (self.orchestrator.run(chat_id, text, read_only=True) if read_only
                           else self.orchestrator.run(chat_id, text))
            finally:
                self.running.pop(chat_id, None)
            if outcome is not None:
                self.after_task(outcome, text, time.monotonic() - started)

        thread = threading.Thread(target=work, daemon=True)
        thread.start()
        return thread


LONG_TASK = 30          # seconds: a task that took longer gets a "done" notification


def _after_task(self, outcome, text: str, took: float) -> None:
    short = " ".join(text.split())[:70]
    try:
        waiting = any(s.status == "waiting_approval" for s in self.store.steps(outcome.task_id))
    except Exception:
        waiting = False
    if waiting:
        self.notify("waiting", f"Waiting for your yes: {short}")
    elif took >= LONG_TASK and outcome.status in ("done", "failed"):
        self.notify("task", ("Done: " if outcome.status == "done" else "Couldn't finish: ") + short)


Services.after_task = _after_task


def apply_settings(services: "Services") -> None:
    """Make changed models, providers and the budget cap take effect without a restart."""
    from prometheus.models.router import build_routes

    s, router = services.settings, services.router
    services.budget.cap_usd = s.budget_usd
    from prometheus.agents.email_agent import EmailAgent

    EmailAgent.private_outputs = not s.email_cloud_ok
    services.orchestrator.step_tries = max(1, min(int(s.step_tries), 6))
    services.orchestrator.task_budget = max(0.01, min(float(s.task_budget_usd), 10.0))
    router.head = s.head_model
    router.paid_needs_yes = bool(getattr(s, "paid_needs_yes", True))
    services.policy.extra_roots = usable_extra_folders(s, services.home)
    for name, model in (("openrouter", s.openrouter_model), ("anthropic", s.anthropic_model),
                        ("ollama", s.ollama_model)):
        provider = router.providers.get(name)
        if provider is not None and hasattr(provider, "model") and model:
            provider.model = model
    from prometheus.models.router import provider_on

    for name, provider in router.providers.items():
        if hasattr(provider, "enabled") and hasattr(provider, "info"):
            provider.enabled = provider_on(provider.info, s)
            if s.provider_models.get(name):
                provider.model = s.provider_models[name]
    for purpose, entries in build_routes(s).items():
        if purpose != "private" and any(e.split(":")[0] in router.providers for e in entries):
            router.routes[purpose] = entries


def usable_extra_folders(settings: Settings, home: Path) -> list[Path]:
    """The extra places from Settings that are still safe to use (checked again at every start)."""
    from prometheus.safety.paths import extra_root_problem

    return [Path(f).resolve() for f in settings.extra_folders if not extra_root_problem(f, home)]


def build_services(settings: Settings, providers: dict[str, Provider] | None = None,
                   home: Path | None = None) -> Services:
    data = data_dir()
    home = Path(home or Path.home())
    store = Store(data / "memory.db")
    store.mark_interrupted()
    if providers is None and settings.embed_model:
        try:
            from prometheus.models.ollama import OllamaEmbedder

            store.embedder = OllamaEmbedder(settings.embed_model, settings.ollama_url)
        except ValueError:   # Ollama is not on this laptop: never send file text elsewhere to embed it
            store.embedder = None
    sweep_wifi_profiles()
    from prometheus.memory.archive import ChatArchive, default_folder

    memory_folder = Path(settings.memory_folder).expanduser() if settings.memory_folder else default_folder(home)
    archive = ChatArchive(store, memory_folder)
    store.listeners.append(archive.sync)
    threading.Thread(target=archive.sync_all, daemon=True).start()   # file away chats from older versions
    policy = PathPolicy(home=home, data_dir=data, code_dir=CODE_DIR, protected=(memory_folder,),
                        extra_roots=usable_extra_folders(settings, home))
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
    router.head = settings.head_model
    router.paid_needs_yes = bool(getattr(settings, "paid_needs_yes", True))
    prompts = PromptStore(data / "prompts")
    patcher = Patcher(PACKAGE_DIR, approvals, data / "backups")
    approvals.register("apply_patch", patcher.apply)
    from prometheus.agents.files import recycle_handler

    approvals.register("recycle_files", lambda payload: recycle_handler(payload, policy, files))
    approvals.register("clone_repo", lambda payload: clone_handler(payload, data / "quarantine"))
    approvals.register("open_link", open_link_handler)
    approvals.register("open_local", lambda payload: open_local_handler(payload, policy))
    def set_model(payload: dict) -> str:
        provider, model = str(payload["provider"]), str(payload["model"])
        field = {"openrouter": "openrouter_model", "anthropic": "anthropic_model", "ollama": "ollama_model"}[provider]
        from prometheus import undo

        undo.record(store, "setting", f"switching {provider} to {model}",
                    {"field": field, "old": getattr(settings, field), "provider": provider})
        setattr(settings, field, model)
        save_settings(settings)
        live = router.providers.get(provider)
        if live is not None:
            live.model = model
        return f"Now using {model} for {provider}."

    approvals.register("set_model", set_model)
    approvals.register("github_action", lambda payload: github_action_handler(payload, GitHubAgent()))
    approvals.register("laptop_action", lambda payload: laptop_action_handler(payload, Laptop()))
    # installing an app the owner tried to open: asked right in the chat, and only for that one package
    from prometheus.agents.email_agent import send_email_handler

    approvals.register("send_email", send_email_handler)
    from prometheus.agents.google_agent import event_handler

    approvals.register("google_event", event_handler)
    from prometheus.agents import browser_agent
    from prometheus.connectors.browser import BrowserSession

    browser_agent.SESSION = BrowserSession(data / "browser")
    approvals.register("browser_click", browser_agent.click_handler)
    approvals.register("browser_password", browser_agent.password_handler, needs_secret=True)
    from prometheus.agents import code_agent

    approvals.register("code_run", code_agent.run_handler)
    approvals.register("code_network", code_agent.run_handler)
    from prometheus.agents import apps_agent
    from prometheus.connectors.manager import ConnectionManager

    apps_agent.MANAGER = ConnectionManager(settings)
    approvals.register("mcp_call", apps_agent.mcp_call_handler)
    approvals.register("install_app_chat", lambda payload: laptop_action_handler(payload, Laptop()))
    approvals.register("wifi_password", lambda payload, secret: wifi_password_handler(payload, secret, Laptop()),
                       needs_secret=True)

    antigravity = AntigravityRunner(store, budget, killswitch)

    approvals.register("antigravity_job", antigravity.submit)

    (data / "skills").mkdir(exist_ok=True)
    skills = SkillBook(PACKAGE_DIR / "skills", data / "skills")

    def make_context(task_id: int, step_id: int) -> AgentContext:
        return AgentContext(task_id=task_id, step_id=step_id, store=store, router=router, policy=policy, files=files,
                            permissions=permissions, approvals=approvals, killswitch=killswitch, home=home,
                            skills=skills)

    orchestrator = Orchestrator(store, router, REGISTRY, make_context, killswitch, prompts.folder, skills=skills)
    orchestrator.extra_catalog = apps_agent.MANAGER.catalog_text
    orchestrator.settings = settings          # Features switches and Stop everything
    from prometheus import features as _features

    _features.CURRENT = settings
    if getattr(settings, "paused", False):
        killswitch.trigger("Stopped by the user (Stop all is still on)")   # stays stopped after a restart
    orchestrator.step_tries = max(1, min(int(settings.step_tries), 6))
    orchestrator.task_budget = max(0.01, min(float(settings.task_budget_usd), 10.0))
    services = Services(settings, home, store, policy, journal, files, permissions, approvals, budget, killswitch,
                        router, orchestrator, patcher, prompts, skills)
    services.archive = archive
    from prometheus.voice.service import VoiceService

    services.voice = VoiceService(services, data / "voice")
    from prometheus.autoupdate import AutoUpdater

    services.updater = AutoUpdater(services)
    from prometheus.daily import DailyJobs
    from prometheus.repo_of_day import RepoOfTheDay

    services.daily = DailyJobs(services)
    from prometheus.reminders import ReminderClock

    def _notify(text: str) -> None:
        import sys

        if sys.platform == "win32":
            try:
                from prometheus.laptop import Laptop

                Laptop().notify(text)
            except Exception:
                pass
        _to_phone(text)

    def _to_phone(text: str) -> None:
        """The phone too: straight from the cloud copy, or handed to it by the laptop."""
        import threading as _th

        def go():
            from prometheus import phone, sync

            try:
                if getattr(settings, "role", "laptop") == "cloud":
                    phone.push(store, "Prometheus", text)
                elif getattr(settings, "cloud_url", "") and sync.token(create=False):
                    import httpx

                    httpx.post(settings.cloud_url.rstrip("/") + "/api/notify", json={"title": "Prometheus", "text": text},
                               headers={"Authorization": f"Bearer {sync.token(create=False)}"}, timeout=15)
                else:
                    phone.push(store, "Prometheus", text)
            except Exception:
                pass

        _th.Thread(target=go, daemon=True).start()

    services.notifier = _notify

    def _budget_warning(text: str) -> None:
        services.notify("budget", text)
        chat = services.briefing.chat() if services.briefing is not None else 0
        if chat:
            store.add_message(chat, "assistant", f"💸 {text}", "budget")

    budget.on_warn = _budget_warning
    services.reminders = ReminderClock(services, notify=_notify)
    import sys as _sys

    from prometheus.watchers import Watchers

    if _sys.platform == "win32":
        from prometheus.laptop import Laptop as _Laptop

        _lp = _Laptop()
    else:
        _lp = None
    services.watchers = Watchers(services, laptop=_lp, notify=lambda text: services.notify("alert", text))
    services.laptop = _lp
    services.browser = browser_agent.SESSION
    code_agent.SERVICES = services
    from prometheus.agents import tools_agent
    from prometheus.own_tools import OwnTools

    tools_agent.TOOLS, tools_agent.SERVICES = OwnTools(data / "tools"), services
    approvals.register("approve_tool", tools_agent.approve_handler)
    orchestrator.extra_sections.append(("Own tools (use the tools agent with action run)", tools_agent.TOOLS.catalog))
    from prometheus.briefing import BriefingClock

    services.briefing = BriefingClock(services, notify=lambda text: services.notify("briefing", text))
    from prometheus import briefing as _briefing
    from prometheus import quick as _quick

    _quick.WEATHER = lambda day: _briefing.weather(services, day_offset=day)
    from prometheus.agents import briefing_agent

    briefing_agent.SERVICES = services
    from prometheus import file_index as _fi

    services.file_index = _fi.FileIndex(store, policy, usable_extra_folders(settings, home))
    _fi.CURRENT = services.file_index
    from prometheus import photos as _ph

    services.photos = _ph.PhotoIndex(store, policy)
    _ph.CURRENT = services.photos
    approvals.register("photo_sort", lambda payload: _ph.sort_handler(payload, policy, files))
    approvals.register("add_skill", lambda payload: RepoOfTheDay(services).add(payload))
    orchestrator.interceptors.append(lambda chat_id, text: RepoOfTheDay(services).answer(chat_id, text))
    from prometheus import resume as _resume

    orchestrator.interceptors.append(lambda chat_id, text: _resume.answer(store, chat_id, text))
    services.resumer = _resume.Resumer(services)
    from prometheus.mcp_server import action_handler
    from prometheus.mcp_server import token as _mcp_token

    _mcp_token()                                  # made once; the stdio bridge reads it from the key store

    approvals.register("mcp_action", action_handler(services))
    from prometheus import undo as _undo
    from prometheus.agents import google_agent as _ga

    _undo.SERVICES, _ga.UNDO_STORE = services, store
    approvals.register("undo", _undo.handler)
    from prometheus import study as _study
    from prometheus.agents import study_agent as _sa

    _sa.STUDY = _study.Study(store, router, settings)
    _sa.STUDY.folder = home / "Documents" / "Prometheus Study"
    orchestrator.interceptors.append(_sa.STUDY.answer)
    from prometheus import money as _money
    from prometheus.agents import money_agent as _ma

    _ma.MONEY = _money.Money(store, settings)
    import sys as _sys2

    from prometheus import clipboard as _clip

    def _ocr(path):
        return _ocr.looker.text(path)

    _ocr.looker = None
    try:
        from prometheus.photos import Looker as _Looker

        _ocr.looker = _Looker()
    except Exception:
        pass
    services.clips = _clip.ClipMemory(store, home, settings,
                                      reader=_clip.windows_clipboard_text if _sys2.platform == "win32" else None,
                                      ocr=_ocr if _ocr.looker is not None else None)
    _clip.CURRENT = services.clips
    from prometheus import focus as _focus
    from prometheus.agents import focus_agent as _fa

    services.focus = _fa.FOCUS = _focus.Focus(services, laptop=_lp)
    from prometheus import health as _health
    from prometheus.agents import health_agent as _ha

    _ha.SERVICES = services
    from prometheus import exams as _exams
    from prometheus.agents import exams_agent as _ea

    _ea.EXAMS, _ea.SERVICES = _exams.Exams(store, router, settings), services
    orchestrator.interceptors.append(_ea.EXAMS.answer_checkin)
    approvals.register("exam_calendar", _ea.calendar_handler)
    from prometheus.agents import lecture_agent as _la

    _la.SERVICES = services
    from prometheus.voice import reader as _reader

    _reader.CURRENT = _reader.Reader(services)
    from prometheus import assignments as _assign
    from prometheus.agents import assignments_agent as _aa

    _aa.TRACKER, _aa.SERVICES = _assign.Assignments(store, settings), services
    approvals.register("assignment_calendar", _aa.calendar_handler)
    from prometheus.agents import reminders_agent as _ra

    _ra.STORE = store
    from prometheus.sync import SyncClock

    services.sync = SyncClock(services)

    def _ok_synced_goal(payload: dict) -> str:
        store._write("UPDATE reminders SET state = 'waiting' WHERE id = ? AND state = 'needs_ok'", (int(payload["id"]),))
        return "OK: this laptop will run that standing goal on schedule."

    approvals.register("synced_goal", _ok_synced_goal)
    from prometheus import phone as _phone

    services.laptop_jobs = _phone.LaptopJobs(services)
    from prometheus.agents import phone_agent as _pa

    _pa.SERVICES = services
    approvals.register("phone_action", _pa.action_handler)

    def _to_laptop(chat_id: int, text: str, plan, read_only: bool = False) -> str:
        """On the cloud copy: a request that needs the laptop (its screen, files or apps) waits for the laptop."""
        if getattr(settings, "role", "laptop") != "cloud":
            return ""
        if not any(st["agent"] in _phone.LAPTOP_AGENTS for st in plan.steps):
            return ""
        _phone.queue_for_laptop(store, dict(store.chats()).get(chat_id, _phone.PHONE_CHAT), text, read_only=read_only)
        from prometheus.sync import laptop_away

        return ("Sent to your laptop: it's on, so it'll do it in a moment and the answer will appear here."
                if not laptop_away(store) else
                "That needs your laptop. It's off right now, so it'll be done as soon as the laptop is on.")

    orchestrator.before_steps = _to_laptop
    approvals.register("standing_goal", _ra.goal_handler)
    from prometheus.agents import screen_agent as _scr

    _scr.SERVICES = services
    approvals.register("screen_click", _scr.screen_click_handler)
    approvals.register("clone_and_check", lambda payload: _scr.clone_and_check_handler(payload, data / "quarantine"))

    def _health_fix(payload: dict) -> str:
        if payload.get("fix") == "settings":
            if services.laptop is None:
                return "That only works on Windows."
            return services.laptop.open_settings_page(str(payload.get("page", "")))
        return "Unknown fix."

    approvals.register("health_fix", _health_fix)
    approvals.register("health_clean", lambda payload: _health.clear_temp())
    return services


__all__ = ["Services", "build_services", "TaskOutcome"]
