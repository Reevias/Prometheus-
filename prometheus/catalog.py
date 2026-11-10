"""Catalog of free AI providers (data from mnfst/awesome-free-llm-apis, CC0), plus what Prometheus knows about each."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA = Path(__file__).resolve().parent / "data" / "free_providers.json"

ID_OVERRIDE = {"Google Gemini": "gemini"}
SKIP = {"OpenRouter"}                      # has its own connector
KEYLESS = {"Kilo Code", "LLM7.io", "OVHcloud AI Endpoints"}
BASE_OVERRIDE = {"Google Gemini": "https://generativelanguage.googleapis.com/v1beta/openai"}
UNSUPPORTED = {
    "Cohere": "uses its own request format, not the common one",
    "Cloudflare Workers AI": "needs your Cloudflare account id inside the address",
}
LOG_WORDS = ("train", "improve products", "log prompts", "logged", "retained")
KNOWN_TO_LOG = {"Google Gemini", "Mistral AI", "Kilo Code", "LLM7.io"}


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    base_url: str
    key_name: str
    keyless: bool
    signup_url: str
    description: str
    models: tuple[str, ...] = field(default_factory=tuple)
    logs_prompts: bool = False
    supported: bool = True
    why_unsupported: str = ""


def _slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", name.lower())).strip("-")


@lru_cache(maxsize=1)
def load_catalog() -> tuple[ProviderInfo, ...]:
    raw = json.loads(DATA.read_text(encoding="utf-8"))
    out = []
    for p in raw["providers"]:
        name = p["name"]
        if name in SKIP:
            continue
        pid = ID_OVERRIDE.get(name) or _slug(name)
        desc = p.get("description", "")
        out.append(ProviderInfo(
            id=pid, name=name, base_url=(BASE_OVERRIDE.get(name) or p["baseUrl"]).rstrip("/"),
            key_name=pid.upper().replace("-", "_") + "_API_KEY", keyless=name in KEYLESS,
            signup_url=p.get("signupUrl", ""), description=desc, models=tuple(p.get("models", [])),
            logs_prompts=True,   # free tiers: assume prompts may be kept or used for training unless proven otherwise
            supported=name not in UNSUPPORTED, why_unsupported=UNSUPPORTED.get(name, "")))
    return tuple(out)


def catalog_updated() -> str:
    return json.loads(DATA.read_text(encoding="utf-8")).get("lastUpdated", "")


# Models are chosen automatically: the strongest general chat model the provider lists first,
# then the next ones as fallbacks if a model is retired or refused.
NOT_CHAT = ("embed", "guard", "whisper", "tts", "audio", "speech", "moderation", "image", "ocr", "rerank",
            "transcribe", "vision-preview", "-rp-", "roleplay", "bge-", "/bge", "-mt-", "translat")
STRONG = ("405b", "235b", "120b", "70b", "72b", "large", "pro", "maverick", "deepseek-v3", "deepseek-r1", "qwen3",
          "gpt-oss", "kimi", "glm-4", "flash", "command-a", "sonnet", "opus")
SMALL = ("nano", "tiny", "mini", "-1b", "-3b", "-7b", "-8b", "1.5b", "2b", "small", "lite")


def model_order(models, provider_name: str = "") -> list[str]:
    """Chat models, best guess first: the provider's own family, then bigger and stronger models.
    Never picks embedding, speech, image or safety models."""
    chat = [m for m in models if isinstance(m, str) and m and not any(w in m.lower() for w in NOT_CHAT)]
    brand = [w for w in re.findall(r"[a-z]{4,}", provider_name.lower()) if w not in ("google", "labs", "endpoints")]

    def score(item):
        pos, m = item
        low = m.lower()
        points = sum(w in low for w in STRONG) * 2 - sum(w in low for w in SMALL) * 3
        points += 3 if any(b in low for b in brand) else 0
        sizes = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)b\b", low)]
        points += min(4.0, max(sizes) / 100) if sizes else 0
        return (-points, pos)

    return [m for _, m in sorted(enumerate(chat), key=score)]
