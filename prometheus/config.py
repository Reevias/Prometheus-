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
    ollama_model: str = "qwen3.5:2b"
    ollama_url: str = "http://127.0.0.1:11434"
    extra_folders: list[str] = field(default_factory=list)   # drives or folders outside the user folder it may use
    memory_folder: str = ""                     # where every chat is saved as files ("" = <home>/Prometheus memory)
    embed_model: str = "nomic-embed-text"       # local model for meaning-based memory search ("" = words only)
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
    return settings


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
}
for _p in load_catalog():
    KEY_NAMES.setdefault(_p.key_name, _p.name)
_SECRETS_LOCK = threading.RLock()
_KEY_VALUE = re.compile(r"^[\x21-\x7e]{4,500}$")   # plain printable characters only, no spaces or line breaks


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
    if name not in KEY_NAMES:
        raise ValueError(f"Unknown key name: {name}")
    if not _KEY_VALUE.match(value):
        raise ValueError("That doesn't look like a valid key.")
    with _SECRETS_LOCK:
        values = _read_env_file()
        values[name] = value
        _write_env_file(values)


def delete_secret(name: str) -> None:
    with _SECRETS_LOCK:
        values = _read_env_file()
        if values.pop(name, None) is not None:
            _write_env_file(values)


def secrets_set() -> dict[str, bool]:
    """Which keys exist (names only, never values)."""
    return {name: bool(get_secret(name)) for name in KEY_NAMES}
