"""Picks a model per purpose and switches by itself. Enforces privacy, budget and the kill switch.

A route is an ordered list of entries, each "provider" or "provider:model". The router tries them in order,
skips any that are unavailable or resting after a failure, and drops to free/local ones when the cap is reached.
"""
from __future__ import annotations

import re
import time
from typing import Callable

from prometheus.config import Settings
from prometheus.models.anthropic_provider import AnthropicProvider
from prometheus.models.base import PrivacyViolation, Provider, ProviderUnavailable, Reply
from prometheus.catalog import load_catalog
from prometheus.models.compat import OpenAICompatProvider
from prometheus.models.ollama import OllamaProvider
from prometheus.models.openrouter import OpenRouterProvider
from prometheus.safety.budget import Budget, BudgetExceeded
from prometheus.safety.killswitch import KillSwitch

REST_SECONDS = 300
BAD_MODEL = re.compile(r"model name|model .*(doesn'?t exist|not found|unknown|invalid)|no such model|"
                       r"(unknown|invalid) model", re.I)
HEAD_PURPOSES = ("plan", "combine")       # the lead AI: plans tasks, answers simple questions, writes final answers


def split_entry(entry: str) -> tuple[str, str | None]:
    name, sep, model = entry.partition(":")
    return name, (model or None) if sep else None


class ModelRouter:
    def __init__(self, providers: dict[str, Provider], routes: dict[str, list[str]], budget: Budget,
                 killswitch: KillSwitch, clock: Callable[[], float] = time.time):
        self.providers = providers
        self.routes = routes
        self.budget = budget
        self.killswitch = killswitch
        self.clock = clock
        self._resting: dict[str, tuple[float, str]] = {}
        self.head = ""          # the lead AI chosen by hand ("" = follow the routes)
        for entry in routes.get("private", []):
            p = providers.get(split_entry(entry)[0])
            if p is None or not p.is_local:
                raise PrivacyViolation(f"'{entry}' is not a local model; personal files may only use local models.")

    def clear_rest(self) -> None:
        """Wake every resting model (called when a key is added or changed)."""
        self._resting.clear()

    def status(self) -> dict[str, bool]:
        return {name: p.available() for name, p in self.providers.items()}

    def status_detail(self) -> list[dict]:
        now = self.clock()
        out = []
        for name, p in self.providers.items():
            until, reason = self._resting.get(name, (0.0, ""))
            resting = until > now
            out.append({"name": name, "available": p.available(), "local": p.is_local, "resting": resting,
                        "reason": reason if resting else "", "minutes_left": int((until - now) // 60) + 1 if resting else 0})
        return out

    def is_free(self, entry: str) -> bool:
        name, model = split_entry(entry)
        p = self.providers.get(name)
        if p is None:
            return False
        model = model or getattr(p, "model", "") or ""
        return bool(p.is_local or getattr(p, "is_free", False) or (name == "openrouter" and model.endswith(":free")))

    def entries_for(self, purpose: str) -> list[str]:
        """The order to try. With a hand-picked lead AI, planning and answers use it first and then only free or
        local models, so a choice made to save money never falls back to a paid one."""
        route = list(self.routes.get(purpose, []))
        if self.head and purpose in HEAD_PURPOSES:
            return [self.head] + [e for e in route if e != self.head and self.is_free(e)]
        return route

    def complete(self, purpose: str, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096) -> Reply:
        self.killswitch.check()
        errors: list[str] = []
        over_budget = False
        for entry in self.entries_for(purpose):
            name, model = split_entry(entry)
            provider = self.providers.get(name)
            if provider is None or not provider.available():
                continue
            if purpose == "private" and not provider.is_local:
                raise PrivacyViolation(f"'{name}' is not local.")
            used_model = model or getattr(provider, "model", "") or ""
            rest_key = next((k for k in (name, f"{name}/{used_model}")
                             if self._resting.get(k, (0.0, ""))[0] > self.clock()), None)
            if rest_key:
                errors.append(f"{rest_key}: resting ({self._resting[rest_key][1]})")
                continue
            free = (provider.is_local or getattr(provider, "is_free", False)
                    or (name == "openrouter" and used_model.endswith(":free")))
            if not free and self.budget.state().stopped:
                over_budget = True   # cap reached: skip paid models, keep looking for a free or local one
                continue
            try:
                reply = provider.complete(messages, system, json_schema=json_schema, max_tokens=max_tokens,
                                          model=model)
            except ProviderUnavailable as e:
                # a wrong model name rests only that model; the provider's other models keep working
                key = f"{name}/{used_model}" if BAD_MODEL.search(str(e)) else name
                self._resting[key] = (self.clock() + REST_SECONDS, str(e))
                errors.append(f"{name}: {e}")
                continue
            self._resting.pop(name, None)
            if not provider.is_local:
                self.budget.record(reply.provider, reply.model, reply.usd, reply.tokens_in, reply.tokens_out)
            return reply
        if over_budget:
            self.budget.ensure_can_spend()
            raise BudgetExceeded("Spending cap reached and no free model is available.")
        message = f"No AI model is available for '{purpose}'. Start Ollama or add an API key in Settings."
        if errors:
            message += " (" + "; ".join(errors) + ")"
        raise ProviderUnavailable(message)


CLOUD_PURPOSES = ("plan", "combine", "research")
CLAUDE_CHOICES = (("claude-opus-5-5", "Claude Opus 5.5 (smartest, $4/$20 per million tokens)"),
                  ("claude-sonnet-5-5", "Claude Sonnet 5.5 ($2/$10 per million tokens)"),
                  ("claude-haiku-5-5", "Claude Haiku 5.5 (cheap, $0.10/$0.50 per million tokens)"))


def head_choices(router: "ModelRouter") -> list[tuple[str, str]]:
    """Lead AIs that can be picked right now: (route entry, label). Only ones that are set up appear."""
    out = []
    for name, p in router.providers.items():
        if not p.available():
            continue
        model = getattr(p, "model", "") or ""
        entry = f"{name}:{model}" if model else name
        if name == "anthropic":
            out += [(f"anthropic:{m}", label) for m, label in CLAUDE_CHOICES]
        elif name == "ollama":
            out.append((entry, f"On this laptop: {model or 'local model'} (free, private, slower)"))
        elif name == "openrouter":
            out.append((entry, f"OpenRouter: {model or 'default'}"))
            out += [(f"openrouter:{m['id']}", f"OpenRouter: {m['name']} ({m['maker']}, ${m['price']} per M tokens)")
                    for m in openrouter_models() if f"openrouter:{m['id']}" != entry]
        else:
            info = getattr(p, "info", None)
            label = info.name if info else name
            out.append((entry, f"{label}: {model} (free)" if model else f"{label} (free)"))
    return out


def provider_on(info, settings: Settings) -> bool:
    """A catalog provider is used when it is supported, not switched off, and either has a key (checked when it is
    called) or needs none and free no-key providers are allowed."""
    if not info.supported or info.id in settings.disabled_providers:
        return False
    return not info.keyless or settings.free_no_key or info.id in settings.enabled_providers


# Which AI writes research reports (Settings → AI → Research brain). Kimi runs on Moonshot's servers via OpenRouter.
def openrouter_models() -> list[dict]:
    """A hand-picked list of strong models on OpenRouter (prometheus/data/openrouter_models.json)."""
    import json as _json
    from pathlib import Path as _Path

    try:
        return _json.loads((_Path(__file__).resolve().parent.parent / "data" / "openrouter_models.json")
                           .read_text(encoding="utf-8"))["models"]
    except (OSError, ValueError, KeyError):
        return []


RESEARCH_BRAINS = {"auto": "", "claude": "anthropic"}
RESEARCH_LABELS = {"auto": "Automatic (as before: the cheapest good model)", "claude": "Claude"}
for _m in openrouter_models():
    RESEARCH_BRAINS[_m["id"]] = f"openrouter:{_m['id']}"
    RESEARCH_LABELS[_m["id"]] = f"{_m['name']} ({_m['maker']}; {_m['about']}; ${_m['price']} per M tokens)"


def build_routes(settings: Settings) -> dict[str, list[str]]:
    """The user's routes, plus every free provider that is on: ones with a key first, no-key ones after them,
    the local model last. Never added to the private route."""
    from prometheus.config import get_secret

    routes = {purpose: list(entries) for purpose, entries in settings.routes.items()}
    if settings.openrouter_fast_model and "research" in routes:
        routes["research"] = [f"openrouter:{settings.openrouter_fast_model}" if e == "openrouter" else e
                              for e in routes["research"]]
    brain = getattr(settings, "research_brain", "auto")
    if brain in RESEARCH_BRAINS and RESEARCH_BRAINS[brain] and "research" in routes:
        first = RESEARCH_BRAINS[brain]
        # the chosen research brain goes first; the old route stays behind it as the fallback
        routes["research"] = [first] + [e for e in routes["research"] if e != first]
    on = [p for p in load_catalog() if provider_on(p, settings)]
    keyed = [p for p in on if not p.keyless and get_secret(p.key_name)]
    keyless = [p for p in on if p.keyless]
    for info in keyed + keyless:
        model = settings.provider_models.get(info.id, "")
        entry = f"{info.id}:{model}" if model else info.id
        for purpose in CLOUD_PURPOSES:
            names = routes.setdefault(purpose, [])
            if not any(split_entry(e)[0] == info.id for e in names):
                local = [e for e in names if split_entry(e)[0] == "ollama"]
                names[:] = [e for e in names if e not in local] + [entry] + local   # local stays the last resort
    return routes


def build_router(settings: Settings, budget: Budget, killswitch: KillSwitch) -> ModelRouter:
    providers: dict[str, Provider] = {
        "openrouter": OpenRouterProvider(model=settings.openrouter_model),
        "anthropic": AnthropicProvider(model=settings.anthropic_model),
        "ollama": OllamaProvider(settings.ollama_model, settings.ollama_url),
    }
    for info in load_catalog():
        providers[info.id] = OpenAICompatProvider(info, model=settings.provider_models.get(info.id, ""),
                                                  enabled=provider_on(info, settings))
    return ModelRouter(providers, build_routes(settings), budget, killswitch)
