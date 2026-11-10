"""Picks a provider per purpose. Enforces privacy, budget and the kill switch."""
from __future__ import annotations

from prometheus.config import Settings
from prometheus.models.anthropic_provider import AnthropicProvider
from prometheus.models.base import PrivacyViolation, Provider, ProviderUnavailable, Reply
from prometheus.models.gemini import GeminiProvider
from prometheus.models.ollama import OllamaProvider
from prometheus.safety.budget import Budget
from prometheus.safety.killswitch import KillSwitch


class ModelRouter:
    def __init__(self, providers: dict[str, Provider], routes: dict[str, list[str]], budget: Budget,
                 killswitch: KillSwitch):
        self.providers = providers
        self.routes = routes
        self.budget = budget
        self.killswitch = killswitch
        for name in routes.get("private", []):
            p = providers.get(name)
            if p is None or not p.is_local:
                raise PrivacyViolation(f"'{name}' is not a local model; personal files may only use local models.")

    def status(self) -> dict[str, bool]:
        return {name: p.available() for name, p in self.providers.items()}

    def complete(self, purpose: str, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096) -> Reply:
        self.killswitch.check()
        errors = []
        for name in self.routes.get(purpose, []):
            provider = self.providers.get(name)
            if provider is None or not provider.available():
                continue
            if purpose == "private" and not provider.is_local:
                raise PrivacyViolation(f"'{name}' is not local.")
            if not provider.is_local:
                self.budget.ensure_can_spend()
            try:
                reply = provider.complete(messages, system, json_schema=json_schema, max_tokens=max_tokens)
            except ProviderUnavailable as e:
                errors.append(f"{name}: {e}")
                continue
            if not provider.is_local:
                self.budget.record(reply.provider, reply.model, reply.usd, reply.tokens_in, reply.tokens_out)
            return reply
        message = f"No AI model is available for '{purpose}'. Start Ollama or add an API key in Settings."
        if errors:
            message += " (" + "; ".join(errors) + ")"
        raise ProviderUnavailable(message)


def build_router(settings: Settings, budget: Budget, killswitch: KillSwitch) -> ModelRouter:
    providers: dict[str, Provider] = {
        "anthropic": AnthropicProvider(model=settings.anthropic_model),
        "ollama": OllamaProvider(settings.ollama_model, settings.ollama_url),
        "gemini": GeminiProvider(),
    }
    return ModelRouter(providers, settings.routes, budget, killswitch)
