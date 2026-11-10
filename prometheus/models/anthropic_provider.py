"""Claude through the Anthropic API (needs ANTHROPIC_API_KEY; billed per use)."""
from __future__ import annotations

from prometheus.config import get_secret
from prometheus.models.base import ProviderUnavailable, Reply

# USD per million tokens (input, output). Unknown models are charged like Opus so the cap errs on the safe side.
PRICES = {"claude-opus-5-5": (4.0, 20.0), "claude-sonnet-5-5": (2.0, 10.0), "claude-haiku-5-5": (0.10, 0.50)}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = ("claude-opus-5", "claude-sonnet-5-5", "claude-fable")   # Haiku has no server-side fallback


class AnthropicProvider:
    is_local = False

    def __init__(self, model: str = "claude-opus-5-5", client=None, name: str = "anthropic",
                 effort: str = "medium", client_factory=None):
        self.name = name
        self.model = model
        self.effort = effort
        self._client = client
        self._fixed_client = client is not None
        self._client_key = None
        self._client_factory = client_factory

    def available(self) -> bool:
        return bool(get_secret("ANTHROPIC_API_KEY"))

    def _get_client(self):
        if self._fixed_client:
            return self._client
        key = get_secret("ANTHROPIC_API_KEY")
        if self._client is None or key != self._client_key:   # the key was added or changed in Settings
            if self._client_factory is not None:
                self._client = self._client_factory(key)
            else:
                import anthropic

                self._client = anthropic.Anthropic(api_key=key)
            self._client_key = key
        return self._client

    def complete(self, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096, model: str | None = None) -> Reply:
        model = model or self.model
        if not self.available():
            raise ProviderUnavailable("No Claude API key set.")
        output_config: dict = {"effort": self.effort}
        if json_schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": json_schema}
        try:
            extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"} if model.startswith(FALLBACK_MODELS) else {}
            resp = self._get_client().beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config=output_config,
                **extra,
            )
        except Exception as e:  # network, auth, rate limit: the router tries the next provider
            if isinstance(e, ProviderUnavailable):
                raise
            raise ProviderUnavailable(f"Claude error: {type(e).__name__}") from e
        if resp.stop_reason == "refusal":
            raise ProviderUnavailable("Claude refused this request.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        tin, tout = resp.usage.input_tokens, resp.usage.output_tokens
        price_in, price_out = PRICES.get(model, PRICES["claude-opus-5-5"])
        return Reply(text=text, provider=self.name, model=model, tokens_in=tin, tokens_out=tout,
                     usd=(tin * price_in + tout * price_out) / 1_000_000)
