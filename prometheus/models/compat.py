"""One connector for every provider that speaks the common "chat completions" format (most free ones do)."""
from __future__ import annotations

import httpx

from prometheus.catalog import ProviderInfo, model_order
from prometheus.config import get_secret
from prometheus.models.base import ProviderUnavailable, Reply

REASONS = {400: "it rejected the request (check the model name)", 401: "the key was rejected", 402: "out of credit",
           403: "not allowed", 404: "that model name doesn't exist there", 408: "timed out", 429: "rate limited"}


class _ModelRefused(ProviderUnavailable):
    """This model was refused or doesn't exist; another model from the same provider may work."""


class OpenAICompatProvider:
    is_local = False
    is_free = True   # free tiers; if billing is switched on at the provider, that spend is not tracked here

    MODEL_TRIES = 3          # a retired or refused model makes it try the next one by itself

    def __init__(self, info: ProviderInfo, model: str = "", enabled: bool = False, http: httpx.Client | None = None):
        self.info = info
        self.name = info.id
        self.choices = model_order(info.models, info.name)
        self.model = model or (self.choices[0] if self.choices else (info.models[0] if info.models else ""))
        self.enabled = enabled
        self.http = http or httpx.Client(timeout=httpx.Timeout(180.0, connect=10.0))

    def _key(self) -> str | None:
        return get_secret(self.info.key_name)

    def available(self) -> bool:
        if not self.enabled or not self.info.supported or not self.model:
            return False
        return self.info.keyless or bool(self._key())

    def complete(self, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096, model: str | None = None) -> Reply:
        if not self.available():
            raise ProviderUnavailable(f"{self.info.name} is not set up.")
        if model:
            return self._call(messages, system, json_schema, max_tokens, model)
        tried = [self.model] + [m for m in self.choices if m != self.model]
        last: ProviderUnavailable | None = None
        for candidate in tried[: self.MODEL_TRIES]:
            try:
                reply = self._call(messages, system, json_schema, max_tokens, candidate)
            except _ModelRefused as e:
                last = e
                continue
            self.model = candidate          # remember the one that works
            return reply
        raise last or ProviderUnavailable(f"{self.info.name} has no working model.")

    def _call(self, messages: list[dict], system: str, json_schema: dict | None, max_tokens: int,
              model: str) -> Reply:
        sys_text = system + ("\n\nReply with JSON only, no other text." if json_schema is not None else "")
        body = {"model": model, "messages": [{"role": "system", "content": sys_text}, *messages],
                "max_tokens": max_tokens}
        headers = {}
        key = self._key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            resp = self.http.post(f"{self.info.base_url}/chat/completions", json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"{self.info.name} can't be reached ({type(e).__name__}).") from e
        if resp.status_code in (400, 404, 422):
            raise _ModelRefused(f"{self.info.name}: {REASONS.get(resp.status_code, f'error {resp.status_code}')}.")
        if resp.status_code != 200:
            raise ProviderUnavailable(f"{self.info.name}: {REASONS.get(resp.status_code, f'error {resp.status_code}')}.")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"] or ""
            usage = data.get("usage") or {}
            tin, tout = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        except (ValueError, KeyError, IndexError, TypeError) as e:
            raise ProviderUnavailable(f"{self.info.name} sent an unreadable reply.") from e
        return Reply(text=text, provider=self.name, model=model, tokens_in=tin, tokens_out=tout, usd=0.0)
