"""Claude through the Anthropic API (needs ANTHROPIC_API_KEY; billed per use)."""
from __future__ import annotations

from prometheus.config import get_secret
from prometheus.models.base import ProviderUnavailable, Reply

PRICE_IN = 4.0 / 1_000_000   # claude-opus-5-5, USD per input token
PRICE_OUT = 20.0 / 1_000_000  # USD per output token
FALLBACK_BETA = "server-side-fallback-2026-07-01"


class AnthropicProvider:
    is_local = False

    def __init__(self, model: str = "claude-opus-5-5", client=None, name: str = "anthropic",
                 effort: str = "high"):
        self.name = name
        self.model = model
        self.effort = effort
        self._client = client

    def available(self) -> bool:
        return bool(get_secret("ANTHROPIC_API_KEY"))

    def _get_client(self):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=get_secret("ANTHROPIC_API_KEY"))
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
            resp = self._get_client().beta.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                thinking={"type": "adaptive"},
                output_config=output_config,
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as e:  # network, auth, rate limit: the router tries the next provider
            if isinstance(e, ProviderUnavailable):
                raise
            raise ProviderUnavailable(f"Claude error: {type(e).__name__}") from e
        if resp.stop_reason == "refusal":
            raise ProviderUnavailable("Claude refused this request.")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
        tin, tout = resp.usage.input_tokens, resp.usage.output_tokens
        return Reply(text=text, provider=self.name, model=model, tokens_in=tin, tokens_out=tout,
                     usd=tin * PRICE_IN + tout * PRICE_OUT)
