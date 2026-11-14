"""Every feature with its own on/off switch (Settings → Features), and the stop-everything switch.

A feature either has its own older setting (attr, e.g. clip_on) or is switched off by being listed in
settings.features_off. Agents tied to a switched-off feature are not offered to the planner and refuse to run.

Stop everything (settings.paused): every background job, watcher, sync and connection stops, running tasks are
stopped, and nothing that acts outside (email, phone, browser, GitHub, code, other apps) runs until it's switched
back on. Chat answers still work, and reminders you set yourself still ring.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Feature:
    key: str
    label: str
    group: str
    attr: str = ""                    # an older on/off setting this switch uses
    agents: tuple[str, ...] = ()      # agents that only work while the feature is on


FEATURES: tuple[Feature, ...] = (
    # Daily life
    Feature("briefing_morning", "Morning briefing", "Daily life", attr="briefing_morning_on"),
    Feature("briefing_evening", "Evening wrap-up", "Daily life", attr="briefing_evening_on"),
    Feature("weekly_report", "Weekly report card (Sundays)", "Daily life"),
    Feature("reminders", "Reminders and timers", "Daily life", agents=("reminders",)),
    Feature("instant", "Instant answers (time, weather, opening apps) without AI", "Daily life"),
    Feature("money", "Money tracker (bank and UPI emails, budgets)", "Daily life", attr="money_on", agents=("money",)),
    Feature("focus", "Focus mode", "Daily life", agents=("focus",)),
    Feature("clipboard", "Clipboard and screenshot memory", "Daily life", attr="clip_on", agents=("clipboard",)),
    Feature("health", "Weekly laptop health check", "Daily life", agents=("health",)),
    Feature("repo_of_day", "Repos of the day (top 10 trending)", "Daily life", attr="repo_of_day"),
    Feature("jobs", "Ready-made jobs (notes to actions, news digest, PDFs to a sheet)", "Daily life", agents=("jobs",)),
    # Study
    Feature("study", "Study coach (flashcards, quizzes)", "Study", agents=("study",)),
    Feature("study_nudge", "Daily 'cards are due' reminder", "Study"),
    Feature("exams", "Exam planner posts and check-ins", "Study", agents=("exams",)),
    Feature("assignments", "Deadlines from college emails", "Study", agents=("assignments",)),
    Feature("homework", "Homework checker", "Study", agents=("homework",)),
    Feature("lecture", "Lecture recordings to notes", "Study", agents=("lecture",)),
    Feature("reader", "Read aloud", "Study", agents=("reader",)),
    # Files and memory
    Feature("file_search", "Search inside files (reads new files at night)", "Files and memory"),
    Feature("photos", "Photo tags and text (reads new photos at night)", "Files and memory", agents=("photos",)),
    Feature("tidy", "Nightly memory tidy-up and learning", "Files and memory"),
    Feature("file_sorting", "Sorting and moving files", "Files and memory", agents=("file_sorter",)),
    Feature("obsidian", "Obsidian notes", "Files and memory", agents=("obsidian",)),
    # Watching
    Feature("email_alerts", "New email alerts", "Watching"),
    Feature("watch_battery", "Low battery warning", "Watching", attr="watch_battery"),
    Feature("watch_disk", "Disk almost full warning", "Watching", attr="watch_disk"),
    Feature("watch_cpu", "CPU busy warning", "Watching", attr="watch_cpu"),
    Feature("watch_folders", "Watched folders", "Watching"),
    Feature("webhooks", "Webhook address for other apps", "Watching"),
    # Computer and phone
    Feature("screen", "Seeing the screen", "Computer and phone", agents=("screen",)),
    Feature("apps", "Opening apps and changing laptop settings", "Computer and phone",
            agents=("apps", "system", "settings", "opener")),
    Feature("browser", "Browser control", "Computer and phone", agents=("browser",)),
    Feature("code", "Running code (sandbox)", "Computer and phone", agents=("code",)),
    Feature("phone", "Phone control", "Computer and phone", agents=("phone",)),
    Feature("laptop_jobs", "Jobs sent from the phone to the laptop", "Computer and phone"),
    # Online
    Feature("email", "Email (reading and drafting)", "Online", agents=("email",)),
    Feature("google", "Google Calendar and Drive", "Online", agents=("google", "calendar")),
    Feature("research", "Web research", "Online", agents=("research", "wikipedia")),
    Feature("resume", "Try again by itself after an AI limit", "Online"),
    Feature("translate", "Translating text, files, web pages and the screen", "Online", agents=("translate",)),
    Feature("github", "GitHub and new skills from repos", "Online", agents=("github", "repo_scout", "antigravity")),
    Feature("sync", "Sync with the cloud copy", "Online"),
    Feature("backup", "Nightly backup to Google Drive", "Online", attr="backup_on"),
    Feature("auto_update", "Install updates by itself", "Online", attr="auto_update"),
)
BY_KEY = {f.key: f for f in FEATURES}
CURRENT = None          # the app's settings, for background loops that don't carry them
GROUPS = tuple(dict.fromkeys(f.group for f in FEATURES))
# what may still run while everything is stopped: answering, memory, documents, the user's own reminders
SAFE_WHEN_PAUSED = {"translate", "files", "documents", "guide", "reminders", "undo", "study", "exams", "assignments", "reader",
                    "photos", "tools"}


def on(settings, key: str) -> bool:
    """True unless the feature is switched off (or everything is stopped, for background jobs)."""
    f = BY_KEY.get(key)
    if f is None or settings is None:
        return True
    if f.attr:
        return bool(getattr(settings, f.attr, True))
    return key not in (getattr(settings, "features_off", None) or [])


def running(settings, key: str) -> bool:
    """For background jobs: the feature is on and Prometheus isn't stopped."""
    settings = settings if settings is not None else CURRENT
    return not paused(settings) and on(settings, key)


def paused(settings) -> bool:
    return bool(getattr(settings, "paused", False))


def set_on(settings, key: str, value: bool) -> None:
    f = BY_KEY[key]
    if f.attr:
        setattr(settings, f.attr, bool(value))
        return
    off = [k for k in (getattr(settings, "features_off", None) or []) if k != key]
    if not value:
        off.append(key)
    settings.features_off = off


def agent_on(settings, agent: str) -> bool:
    """The owner's own switch for this agent (Agents page). True unless switched off there."""
    return agent not in (getattr(settings, "agents_off", None) or [])


def set_agent_on(settings, agent: str, value: bool) -> None:
    off = [a for a in (getattr(settings, "agents_off", None) or []) if a != agent]
    if not value:
        off.append(agent)
    settings.agents_off = off


def skill_on(settings, name: str) -> bool:
    """The owner's switch for this skill (Skills page). An off skill is never offered to the planner."""
    return name not in (getattr(settings, "skills_off", None) or [])


def set_skill_on(settings, name: str, value: bool) -> None:
    off = [n for n in (getattr(settings, "skills_off", None) or []) if n != name]
    if not value:
        off.append(name)
    settings.skills_off = off


def feature_of(agent: str):
    """The Features switch this agent belongs to, if any."""
    return next((f for f in FEATURES if agent in f.agents), None)


def agent_blocked(settings, agent: str) -> str:
    """Why this agent may not run now ("" = it may)."""
    if settings is None:
        return ""
    if paused(settings) and agent not in SAFE_WHEN_PAUSED:
        return "Everything is stopped (Settings → Features → Stop everything). Switch it back on to do this."
    if not agent_on(settings, agent):
        return f"The {agent.replace('_', ' ')} agent is switched off (Agents page)."
    for f in FEATURES:
        if agent in f.agents and not on(settings, f.key):
            return f"{f.label} is switched off (Settings → Features)."
    return ""


def view(settings) -> list[tuple[str, list[dict]]]:
    return [(g, [{"key": f.key, "label": f.label, "on": on(settings, f.key)} for f in FEATURES if f.group == g])
            for g in GROUPS]
