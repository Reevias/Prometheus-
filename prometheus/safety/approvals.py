"""Approval queue: risky actions wait for the user's Approve / Reject."""
from __future__ import annotations

from typing import Callable

from prometheus.memory.store import Store


class TryAgain(Exception):
    """A handler raises this when the owner's input was wrong, so the approval stays pending for another go."""


class Approvals:
    def __init__(self, store: Store):
        self.store = store
        self._handlers: dict[str, Callable] = {}
        self._needs_secret: set[str] = set()

    def register(self, kind: str, handler: Callable, needs_secret: bool = False) -> None:
        """needs_secret: the handler is called as handler(payload, secret) with what the owner typed."""
        self._handlers[kind] = handler
        if needs_secret:
            self._needs_secret.add(kind)
        else:
            self._needs_secret.discard(kind)

    def request(self, kind: str, summary: str, payload: dict, secret_label: str = "") -> int:
        if secret_label:
            payload = {**payload, "_secret_label": secret_label}
        approval_id = self.store.add_approval(kind, summary, payload)
        self.store.audit("approvals", "requested", {"id": approval_id, "kind": kind, "summary": summary})
        return approval_id

    def ask(self, kind: str, summary: str, payload: dict, keys: tuple[str, str], chat_id: int = 0,
            broad_label: str = "") -> tuple[str, str | int]:
        """A small approval, asked in the chat. keys = (exact, broad): exact is this very thing (a link, a file);
        broad is its site or folder. If the owner already allowed either, in any chat, it runs straight away:
        returns ("done", result). Otherwise ("waiting", approval id)."""
        exact, broad = keys
        if self.store.allowed(kind, [exact, broad]):
            handler = self._handlers.get(kind)
            if handler is not None:
                result = handler(payload)
                self.store.audit("approvals", "already_allowed", {"kind": kind, "key": exact})
                return "done", result
        payload = {**payload, "_keys": [exact, broad], "_broad_label": broad_label}
        approval_id = self.store.add_approval(kind, summary, payload, chat_id=chat_id, inline=True)
        self.store.audit("approvals", "asked_in_chat", {"id": approval_id, "kind": kind, "summary": summary})
        return "waiting", approval_id

    def resolve(self, approval_id: int, approved: bool, secret: str = "", remember: str = "") -> str:
        item = self.store.approval(approval_id)
        if item.status != "pending":
            return f"Already {item.status}."
        if approved and item.kind in self._needs_secret and not secret:
            return "Type the password first."
        if not self.store.claim_approval(approval_id):        # a second click at the same moment loses here
            return "Already being handled."
        if not approved:
            self.store.set_approval_status(approval_id, "rejected")
            result, status = "rejected", "rejected"
        else:
            handler = self._handlers.get(item.kind)
            try:
                if handler is None:
                    raise RuntimeError(f"No handler for {item.kind}")
                if item.kind in self._needs_secret:
                    result = handler(item.payload, secret)      # the secret is passed on and then forgotten
                else:
                    result = handler(item.payload)
                status = "approved"
            except TryAgain as e:                                # wrong input: leave it waiting
                self.store.set_approval_status(approval_id, "pending")
                return str(e)
            except Exception as e:  # handler failures are recorded, never crash the app
                result, status = f"Failed: {e}", "failed"
            self.store.set_approval_status(approval_id, status)
            keys = item.payload.get("_keys") or []
            if status == "approved" and keys and remember in ("exact", "broad"):
                key = keys[1] if remember == "broad" and len(keys) > 1 and keys[1] else keys[0]
                label = item.payload.get("_broad_label") if remember == "broad" else item.summary
                self.store.add_allow_rule(item.kind, key, label or key)
        self.store.audit("user", "approval", {"id": approval_id, "kind": item.kind, "status": status,
                                              "result": result})
        return result
