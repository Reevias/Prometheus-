"""OpenRouter: one key, many cloud models (https://openrouter.ai). Cloud = never used for private files."""
from __future__ import annotations

import httpx

from prometheus.config import get_secret
from prometheus.models.base import ProviderUnavailable, Reply

URL = "https://openrouter.ai/api/v1/chat/completions"
REASONS = {401: "the OpenRouter key was rejected", 402: "out of OpenRouter credit", 403: "not allowed for this model",
           404: "that model name doesn't exist on OpenRouter", 408: "timed out", 429: "rate limited"}


class OpenRouterProvider:
    is_local = False

    def __init__(self, model: str = "openrouter/auto", http: httpx.Client | None = None, name: str = "openrouter"):
        self.name = name
        self.model = model
        self.http = http or httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))

    def available(self) -> bool:
        return bool(get_secret("OPENROUTER_API_KEY"))

    def _post(self, body: dict) -> httpx.Response:
        headers = {"Authorization": f"Bearer {get_secret('OPENROUTER_API_KEY')}", "X-Title": "Prometheus"}
        try:
            return self.http.post(URL, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"OpenRouter can't be reached ({type(e).__name__}).") from e

    def complete(self, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096, model: str | None = None) -> Reply:
        if not self.available():
            raise ProviderUnavailable("No OpenRouter key set.")
        model = model or self.model
        body = {"model": model, "messages": [{"role": "system", "content": system}, *messages],
                "max_tokens": max_tokens, "usage": {"include": True}}
        if json_schema is not None:
            body["response_format"] = {"type": "json_schema",
                                       "json_schema": {"name": "reply", "strict": True, "schema": json_schema}}
        resp = self._post(body)
        if resp.status_code == 400 and json_schema is not None:
            # This model can't do structured output: ask for JSON in plain words instead.
            body.pop("response_format")
            body["messages"] = [{"role": "system", "content": system + "\n\nReply with JSON only, no other text."},
                                *messages]
            resp = self._post(body)
        if resp.status_code != 200:
            reason = REASONS.get(resp.status_code, f"error {resp.status_code}")
            raise ProviderUnavailable(f"OpenRouter: {reason}.")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ProviderUnavailable("OpenRouter sent an unreadable reply.") from e
        usage = data.get("usage") or {}
        return Reply(text=text, provider=self.name, model=str(data.get("model") or model),
                     tokens_in=int(usage.get("prompt_tokens") or 0), tokens_out=int(usage.get("completion_tokens") or 0),
                     usd=float(usage.get("cost") or 0.0))
