"""Settings, data folder and secrets."""
from __future__ import annotations

import json
import os
import sys
import re
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from prometheus.catalog import load_catalog


def data_dir() -> Path:
    """Where Prometheus keeps its data (never inside the code folder)."""
    override = os.environ.get("PROMETHEUS_HOME")
    if override:
        path = Path(override)
    elif sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        path = Path(os.environ["LOCALAPPDATA"]) / "Prometheus"
    else:
        path = Path.home() / ".local" / "share" / "prometheus"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _default_routes() -> dict[str, list[str]]:
    """Claude plans and writes answers; its cheapest model and other cheap models do the sub-tasks."""
    return {
        "plan": ["anthropic", "openrouter", "ollama"],
        "combine": ["anthropic", "openrouter", "ollama"],
        "research": ["anthropic:claude-haiku-5-5", "openrouter", "ollama"],
        "private": ["ollama"],
    }


def _old_default_routes() -> list[dict[str, list[str]]]:
    """Routes saved by earlier versions' defaults. They are replaced; routes the owner changed are kept."""
    v1 = {"plan": ["anthropic", "ollama"], "combine": ["anthropic", "ollama"], "research": ["anthropic", "ollama"],
          "private": ["ollama"]}
    v11 = {k: (["openrouter", *v] if k != "private" else v) for k, v in v1.items()}
    return [v1, v11]


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    allow_lan: bool = False
    budget_usd: float = 5.0
    routes: dict[str, list[str]] = field(default_factory=_default_routes)
    ollama_model: str = "granite4.2:3b"   # the private laptop AI (2.2 GB; reads your files)
    ollama_url: str = "http://127.0.0.1:11434"
    voice_enabled: bool = False                 # listen for "Prometheus" and answer out loud
    voice_autostart: bool = False
    owner_name: str = ""                     # shown in the sidebar; asked on first run
    ui_glow: float = 1.0                     # Appearance: glow strength 0.4 … 1.4
    ui_sphere: str = "auto"                  # Appearance: sphere quality auto|low|medium|high
    ui_motion: str = "auto"                  # Appearance: auto (follow Windows) | reduced | full
    ui_text: int = 15                        # Appearance: text size (px)
    ui_density: str = "comfortable"          # Appearance: comfortable | compact
    window_x: int | None = None              # where the desktop window was last (restored if that screen is there)
    window_y: int | None = None
    quick_actions: list = field(default_factory=list)  # Home buttons [{label, fill, icon}]; empty = the defaults
    attach_max_mb: int = 25                  # + button: biggest file (MB)
    attach_max_files: int = 10               # + button: files per message
    attach_keep_days: int = 7                # attachments not in a project are removed after this
    voice_name: str = "af_heart"
    voice_speech: str = "fast"                  # fast | accent | hindi: how commands are turned into words
    voice_engine: str = "auto"                  # auto (ElevenLabs when its key is in) | elevenlabs | local
    eleven_voice: str = "JBFqnCBsd6RMkjVDRZzb"  # the ElevenLabs voice (George)
    voice_clap: bool = True                     # a double clap greets you, gives the brief and starts a conversation
    clap_sensitivity: float = 1.0               # higher hears softer claps (0.3 to 3)
    voice_barge_in: bool = True                 # talking over an answer stops it
    paid_needs_yes: bool = True                 # paid AI only after the owner says yes (free ones are used first)
    code_brain: str = "auto"                    # who plans coding requests: auto (Ripple on OpenRouter), off, or a route entry
    features_off: list = field(default_factory=list)   # Settings → Features: the ones switched off
    agents_off: list = field(default_factory=list)     # Agents page: agents the owner switched off
    skills_off: list = field(default_factory=list)     # Skills page: guides the owner switched off
    paused: bool = False                        # Stop everything: no background jobs or outside actions
    auto_update: bool = True
    auto_restart: bool = True              # after an update, restart by itself once nothing is running
    phone_target: str = ""                 # the phone's wireless-debugging address, e.g. 100.64.1.2:41234
    role: str = "laptop"                   # "laptop", or "cloud" for the always-on copy
    cloud_url: str = ""                    # on the laptop: the cloud copy's private address (Tailscale)
    backup_on: bool = False                # nightly encrypted backup to Google Drive
    research_brain: str = "auto"           # auto, or a model from the list in models/router.py
    code_windows_sandbox: bool = True
    notify_tasks: bool = True              # a task that took over 30 seconds finished
    notify_waiting: bool = True            # a task waits for a yes
    notify_briefing: bool = True
    notify_repo: bool = True
    notify_updates: bool = True
    exam_study_time: str = "17:00"         # daily study block and "today's topics" post
    exam_block_minutes: int = 120
    exam_checkin_time: str = "21:30"       # "did you finish today's topics?"
    assignment_senders: list = field(default_factory=list)   # college/school addresses or domains to read
    read_speed: float = 1.0                # read-aloud speed (0.7 to 1.5)
    health_day: int = 6                    # 0 = Monday … 6 = Sunday; -1 for never
    health_time: str = "19:00"
    focus_dnd: bool = True                 # Windows Do Not Disturb during focus
    focus_apps: list = field(default_factory=lambda: ["WhatsApp", "Discord", "Telegram", "Instagram", "Steam"])
    focus_pomodoro: bool = True
    focus_work: int = 25                   # minutes
    focus_break: int = 5
    clip_on: bool = True                   # remember copied text and screenshot text (7 days, never secrets)
    clip_days: int = 7
    money_on: bool = True                  # read bank and UPI alerts from the connected email
    money_budgets: dict = field(default_factory=dict)   # category -> monthly budget in rupees
    study_cloud: bool = False              # let Claude make cards and mark answers (better; notes are sent)
    study_reminder: str = "18:00"          # daily "cards are due" reminder; "" for none
    tray: bool = True                      # icon by the clock; closing the window keeps Prometheus running      # run scripts in Windows Sandbox when this laptop has it
    repo_of_day: bool = True
    step_tries: int = 3                         # tries per task step, counting the first (planning loop)
    task_budget_usd: float = 0.20               # paid AI one task may use                    # suggest one trending GitHub repository a day for the skill library
    obsidian_vault: str = ""
    email_address: str = ""
    email_via: str = ""                         # "google" = Gmail through Google sign-in; "" = app password
    email_imap_host: str = ""                   # "" = worked out from the address
    email_smtp_host: str = ""
    email_cloud_ok: bool = False
    mcp_servers: list[dict] = field(default_factory=list)
    briefing_morning_on: bool = True            # daily summary: morning briefing
    briefing_morning: str = "08:00"
    briefing_evening_on: bool = True            # and evening wrap-up
    briefing_evening: str = "21:00"
    weather_place: str = ""                     # city for the weather ("" = the laptop's location)
    email_alert_senders: list[str] = field(default_factory=list)   # tell me at once when these email me
    webhook_token: str = ""                     # the secret part of the webhook address (made on first use)
    watch_battery: bool = True                  # say so when the battery drops below 20%
    watch_disk: bool = True                     # say so when the main drive is over 90% full
    watch_cpu: bool = False                     # say so when the CPU is over 90% for 5 minutes
    watch_folders: list[dict] = field(default_factory=list)   # [{path, goal}]: new files there start the goal   # [{name, kind: command|url, target, enabled}]                # let cloud AI read email (off: only the local model)                    # the connected Obsidian vault folder ("" = not connected)                    # install new versions from GitHub by itself                # the speaking voice (Kokoro voice id)               # start Prometheus (and listening) when Windows starts
    head_model: str = ""                        # the lead AI chosen by hand, e.g. "anthropic:claude-haiku-5-5" ("" = auto)
    extra_folders: list[str] = field(default_factory=list)   # drives or folders outside the user folder it may use
    memory_folder: str = ""                     # where every chat is saved as files ("" = <home>/Prometheus memory)
    embed_model: str = "embeddinggemma-2:270m"       # local model for meaning-based memory search ("" = words only)
    anthropic_model: str = "claude-opus-5-5"
    openrouter_model: str = "openrouter/auto"   # strong model: planning and final answers
    github_repo: str = "Reevias/Prometheus-"    # the repository Prometheus may read and update
    openrouter_fast_model: str = ""             # cheaper model for research summaries ("" = same as above)
    enabled_providers: list[str] = field(default_factory=list)       # free providers the user switched on (older versions)
    disabled_providers: list[str] = field(default_factory=list)      # providers the user switched off
    free_no_key: bool = True                    # use free providers that need no key (never for personal files)
    provider_models: dict[str, str] = field(default_factory=dict)    # chosen model per free provider
    password_hash: str = ""
    task_folders_note: str = ""


def _settings_path(path: Path | None) -> Path:
    return path or data_dir() / "settings.json"


def load_settings(path: Path | None = None) -> Settings:
    target = _settings_path(path)
    if not target.exists():
        return Settings()
    raw = json.loads(target.read_text(encoding="utf-8"))
    known = {f.name for f in fields(Settings)}
    settings = Settings(**{k: v for k, v in raw.items() if k in known})
    if settings.routes in _old_default_routes():
        settings.routes = _default_routes()
    # older defaults move to the newer models (a model the owner picked by hand stays)
    if settings.ollama_model in OLD_LOCAL_MODELS:
        settings.ollama_model = Settings.ollama_model
    if settings.embed_model in OLD_EMBED_MODELS:
        settings.embed_model = Settings.embed_model
    return settings


OLD_LOCAL_MODELS = {"qwen3.5:2b"}
OLD_EMBED_MODELS = {"nomic-embed-text"}


def save_settings(settings: Settings, path: Path | None = None) -> None:
    target = _settings_path(path)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp, target)


KEY_NAMES = {
    "OPENROUTER_API_KEY": "OpenRouter",
    "ANTHROPIC_API_KEY": "Claude (Anthropic)",
    "GEMINI_API_KEY": "Gemini (Argon)",
    "GITHUB_TOKEN": "GitHub",
    "EMAIL_PASSWORD": "Email app password",
    "CALENDAR_LINKS": "Calendar links",
    "GOOGLE_CLIENT": "Google client (ID|secret)",
    "GOOGLE_TOKEN": "Google sign-in",
    "BACKUP_KEY": "Backup key (made from your recovery code)",
    "SYNC_TOKEN": "Cloud copy sync key",
    "VAPID_PRIVATE": "Phone notifications key",
    "ELEVENLABS_API_KEY": "ElevenLabs (voice)",
    "OLLAMA_CLOUD_API_KEY": "Ollama cloud",
}
MCP_SECRET = re.compile(r"^MCP_[A-Z0-9_]{1,40}$")   # one secret per connected app (a token, or NAME=value;…)
SPACES_ALLOWED = {"EMAIL_PASSWORD"}             # app passwords are often shown in groups: "abcd efgh ijkl mnop"
LINK_LIST = {"CALENDAR_LINKS"}                  # several https links joined by |
for _p in load_catalog():
    KEY_NAMES.setdefault(_p.key_name, _p.name)
_SECRETS_LOCK = threading.RLock()
_KEY_VALUE = re.compile(r"^[\x21-\x7e]{4,2000}$")   # plain printable characters only, no spaces or line breaks


def _read_env_file() -> dict[str, str]:
    env_file = data_dir() / ".env"
    out: dict[str, str] = {}
    with _SECRETS_LOCK:
        if not env_file.exists():
            return out
        text = env_file.read_text(encoding="utf-8")
    for line in text.split("\n"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if val.strip():
            out[key.strip()] = val.strip()
    return out


def _write_env_file(values: dict[str, str]) -> None:
    env_file = data_dir() / ".env"
    tmp = env_file.with_name(".env.tmp")
    try:
        tmp.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        for attempt in range(6):   # Windows refuses to replace a file another thread is reading
            try:
                os.replace(tmp, env_file)
                break
            except PermissionError:
                if attempt == 5:
                    raise
                time.sleep(0.05)
    finally:
        if tmp.exists():           # never leave a second copy of the keys behind
            tmp.unlink()


def get_secret(name: str) -> str | None:
    """A key saved in the app (<data>/.env) wins; otherwise the environment variable. Never log the result."""
    return _read_env_file().get(name) or os.environ.get(name) or None


def set_secret(name: str, value: str) -> None:
    value = value.strip()
    if name in SPACES_ALLOWED:
        value = "".join(value.split())
    if name in LINK_LIST:
        value = "|".join(u.strip() for u in value.replace(",", "|").replace("\n", "|").split("|") if u.strip())
    if name not in KEY_NAMES and not MCP_SECRET.fullmatch(name):
        raise ValueError(f"Unknown key name: {name}")
    if not _KEY_VALUE.match(value):
        raise ValueError("That doesn't look like a valid key.")
    with _SECRETS_LOCK:
        values = _read_env_file()
        values[name] = value
        _write_env_file(values)


def delete_secret(name: str) -> None:
    """Remove a key: from the app's own file, from this run, and (on Windows) from the user's environment
    variables, where `setx NAME value` puts it. A key set for the whole computer by an admin stays."""
    with _SECRETS_LOCK:
        values = _read_env_file()
        if values.pop(name, None) is not None:
            _write_env_file(values)
    os.environ.pop(name, None)
    if sys.platform == "win32":
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except OSError:
            pass                                  # it wasn't there


def secrets_set() -> dict[str, bool]:
    """Which keys exist (names only, never values)."""
    return {name: bool(get_secret(name)) for name in KEY_NAMES}
