"""Settings, data folder and secrets."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path


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
    return {
        "plan": ["anthropic", "ollama"],
        "combine": ["anthropic", "ollama"],
        "research": ["anthropic", "ollama"],
        "private": ["ollama"],
    }


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8000
    allow_lan: bool = False
    budget_usd: float = 5.0
    routes: dict[str, list[str]] = field(default_factory=_default_routes)
    ollama_model: str = "qwen3.5:2b"
    ollama_url: str = "http://127.0.0.1:11434"
    anthropic_model: str = "claude-opus-5-5"
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
    return Settings(**{k: v for k, v in raw.items() if k in known})


def save_settings(settings: Settings, path: Path | None = None) -> None:
    target = _settings_path(path)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")
    os.replace(tmp, target)


def get_secret(name: str) -> str | None:
    """Environment variable first, then <data>/.env. Never log the result."""
    value = os.environ.get(name)
    if value:
        return value
    env_file = data_dir() / ".env"
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        if key.strip() == name:
            return val.strip() or None
    return None
