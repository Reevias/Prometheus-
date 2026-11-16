"""Answers word by word (M3.5): the two common streaming formats, read the same way for every provider.

OpenAI-style providers (OpenRouter, Coarena and the free ones) send "data: {...}" lines with the next words in
choices[0].delta.content; Ollama sends one JSON line per piece with message.content. Only final answers stream (never
JSON plans), and any provider that can't stream simply gives the whole answer at the end.
"""
from __future__ import annotations

import json
from typing import Callable

import httpx

from prometheus.models.base import ProviderUnavailable


def openai_sse(http: httpx.Client, url: str, body: dict, headers: dict, on_text: Callable[[str], None],
               who: str) -> tuple[str, dict, str]:
    """POSTs with stream on; calls on_text with each new piece. Returns (whole text, usage, model)."""
    body = {**body, "stream": True}
    parts: list[str] = []
    usage: dict = {}
    model = str(body.get("model") or "")
    try:
        with http.stream("POST", url, json=body, headers=headers) as resp:
            if resp.status_code != 200:
                resp.read()
                raise ProviderUnavailable(f"{who}: error {resp.status_code}.")
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                usage = chunk.get("usage") or usage
                model = str(chunk.get("model") or model)
                for choice in chunk.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content") or ""
                    if piece:
                        parts.append(piece)
                        on_text(piece)
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"{who} can't be reached ({type(e).__name__}).") from e
    return "".join(parts), usage, model


def ollama_lines(http: httpx.Client, url: str, body: dict, on_text: Callable[[str], None]) -> tuple[str, dict]:
    body = {**body, "stream": True}
    parts: list[str] = []
    last: dict = {}
    try:
        with http.stream("POST", url, json=body) as resp:
            if resp.status_code != 200:
                resp.read()
                raise ProviderUnavailable(f"Ollama error {resp.status_code}")
            for line in resp.iter_lines():
                try:
                    chunk = json.loads(line)
                except ValueError:
                    continue
                piece = (chunk.get("message") or {}).get("content") or ""
                if piece:
                    parts.append(piece)
                    on_text(piece)
                if chunk.get("done"):
                    last = chunk
    except httpx.HTTPError as e:
        raise ProviderUnavailable(f"Ollama error: {e}") from e
    return "".join(parts), last
