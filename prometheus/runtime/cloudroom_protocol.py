"""How the laptop and Cloudroom (the owner's free Render service) talk (M6 step 6.2).

Every request is signed with the Cloudroom token (HMAC-SHA256 over the method, the path, a timestamp, a one-time nonce
and the body's hash). The token itself never travels: only the signature does. The server refuses anything unsigned,
wrongly signed, older than MAX_SKEW seconds or with a nonce it has already seen. Receipts are signed the same way by
the server, so the laptop knows a receipt really came from its own Cloudroom.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

MAX_SKEW = 300                     # seconds a signed request stays valid
H_TIME, H_NONCE, H_SIG = "X-Prom-Time", "X-Prom-Nonce", "X-Prom-Signature"


def _mac(token: str, text: str) -> str:
    return hmac.new(token.encode("utf-8"), text.encode("utf-8"), hashlib.sha256).hexdigest()


def _base(method: str, path: str, ts: str, nonce: str, body: bytes) -> str:
    return "\n".join([method.upper(), path, ts, nonce, hashlib.sha256(body or b"").hexdigest()])


def sign_headers(token: str, method: str, path: str, body: bytes = b"", now: float | None = None) -> dict[str, str]:
    """The three headers that prove a request comes from the owner's laptop (`path` includes any query)."""
    ts = str(int(time.time() if now is None else now))
    nonce = secrets.token_hex(16)
    return {H_TIME: ts, H_NONCE: nonce, H_SIG: _mac(token, _base(method, path, ts, nonce, body))}


def check(token: str, method: str, path: str, body: bytes, headers, seen_nonce, now: float | None = None) -> bool:
    """True when the signature is right, recent, and its nonce is new (seen_nonce(nonce) records it, False if seen)."""
    ts, nonce, sig = headers.get(H_TIME, ""), headers.get(H_NONCE, ""), headers.get(H_SIG, "")
    if not (ts.isdigit() and nonce and sig) or not token:
        return False
    if abs((time.time() if now is None else now) - int(ts)) > MAX_SKEW:
        return False
    if not hmac.compare_digest(sig, _mac(token, _base(method, path, ts, nonce, body))):
        return False
    return seen_nonce(nonce)


def _canon(fields: dict) -> str:
    return json.dumps({k: fields[k] for k in sorted(fields) if k != "signature"}, sort_keys=True, separators=(",", ":"))


def sign_receipt(token: str, fields: dict) -> dict:
    return {**fields, "signature": _mac(token, "receipt\n" + _canon(fields))}


def verify_receipt(token: str, receipt: dict) -> bool:
    sig = receipt.get("signature", "") if isinstance(receipt, dict) else ""
    return bool(sig) and hmac.compare_digest(sig, _mac(token, "receipt\n" + _canon(receipt)))
