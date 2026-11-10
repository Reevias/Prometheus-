"""Spots API keys, tokens and private keys in text before it leaves the laptop (idea from trufflehog).

Used on everything Prometheus writes outward or into its own code: GitHub issues, comments, pull requests
and self-edits. A match blocks the write; the key itself is never echoed back.
"""
from __future__ import annotations

import re

from prometheus.config import KEY_NAMES, get_secret

PATTERNS = {
    "Anthropic key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "OpenRouter key": r"sk-or-[A-Za-z0-9_-]{20,}",
    "OpenAI-style key": r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}",
    "GitHub token": r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}",
    "Google API key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "AWS access key": r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b",
    "Slack token": r"\bxox[abposr]-[A-Za-z0-9-]{10,}",
    "Hugging Face token": r"\bhf_[A-Za-z0-9]{30,}",
    "private key": r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY",
}
_COMPILED = {label: re.compile(rx) for label, rx in PATTERNS.items()}


def find_secrets(text: str, saved=None) -> list[str]:
    """Names of the kinds of secret found in the text (never the secret itself)."""
    text = text or ""
    found = [label for label, rx in _COMPILED.items() if rx.search(text)]
    saved = saved if saved is not None else [get_secret(name) for name in KEY_NAMES]
    if any(value and len(value) >= 12 and value in text for value in saved):
        found.append("one of your saved keys")
    return list(dict.fromkeys(found))


def secret_problem(text: str, saved=None) -> str:
    found = find_secrets(text, saved)
    if not found:
        return ""
    return f"Blocked: this contains what looks like a secret ({', '.join(found)}). Remove it and try again."
