"""Local models through Ollama (runs on the laptop, free, private)."""
from __future__ import annotations

import time
from urllib.parse import urlsplit

import httpx

from prometheus.models.base import ProviderUnavailable, Reply


LOOPBACK = {"127.0.0.1", "localhost", "::1"}
AVAILABILITY_CACHE_SECONDS = 10


class OllamaProvider:

    def __init__(self, model: str, url: str, http: httpx.Client | None = None, name: str = "ollama"):
        self.name = name
        self.model = model
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=httpx.Timeout(300.0, connect=2.0))
        self._checked = (0.0, False)

    @property
    def is_local(self) -> bool:
        """Local only if Ollama runs on this laptop AND the model is not one of Ollama's cloud models."""
        host = (urlsplit(self.url).hostname or "").lower()
        cloud = self.model.lower().endswith((":cloud", "-cloud"))
        return host in LOOPBACK and not cloud

    def available(self) -> bool:
        now = time.monotonic()
        if self._checked[0] and now - self._checked[0] < AVAILABILITY_CACHE_SECONDS:
            return self._checked[1]
        try:
            ok = self.http.get(f"{self.url}/api/tags", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            ok = False
        self._checked = (now, ok)
        return ok

    def complete(self, messages: list[dict], system: str, json_schema: dict | None = None,
                 max_tokens: int = 4096, model: str | None = None) -> Reply:
        model = model or self.model
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": False,
            "options": {"num_predict": max_tokens},
        }
        if json_schema is not None:
            body["format"] = json_schema
        try:
            resp = self.http.post(f"{self.url}/api/chat", json=body)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ProviderUnavailable(f"Ollama error: {e}") from e
        return Reply(text=data.get("message", {}).get("content", ""), provider=self.name, model=model,
                     tokens_in=int(data.get("prompt_eval_count", 0)), tokens_out=int(data.get("eval_count", 0)))
