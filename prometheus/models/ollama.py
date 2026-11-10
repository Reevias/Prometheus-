"""Local models through Ollama (runs on the laptop, free, private)."""
from __future__ import annotations

import httpx

from prometheus.models.base import ProviderUnavailable, Reply


class OllamaProvider:
    is_local = True

    def __init__(self, model: str, url: str, http: httpx.Client | None = None, name: str = "ollama"):
        self.name = name
        self.model = model
        self.url = url.rstrip("/")
        self.http = http or httpx.Client(timeout=httpx.Timeout(300.0, connect=2.0))

    def available(self) -> bool:
        try:
            return self.http.get(f"{self.url}/api/tags", timeout=2.0).status_code == 200
        except httpx.HTTPError:
            return False

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
