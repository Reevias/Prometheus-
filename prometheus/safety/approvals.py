"""Approval queue: risky actions wait for the user's Approve / Reject."""
from __future__ import annotations

from typing import Callable

from prometheus.memory.store import Store


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

    def resolve(self, approval_id: int, approved: bool, secret: str = "") -> str:
        item = self.store.approval(approval_id)
        if item.status != "pending":
            return f"Already {item.status}."
        if not approved:
            self.store.set_approval_status(approval_id, "rejected")
            result, status = "rejected", "rejected"
        else:
            handler = self._handlers.get(item.kind)
            try:
                if handler is None:
                    raise RuntimeError(f"No handler for {item.kind}")
                if item.kind in self._needs_secret:
                    if not secret:
                        return "Type the password first."
                    result = handler(item.payload, secret)      # the secret is passed on and then forgotten
                else:
                    result = handler(item.payload)
                status = "approved"
            except Exception as e:  # handler failures are recorded, never crash the app
                result, status = f"Failed: {e}", "failed"
            self.store.set_approval_status(approval_id, status)
        self.store.audit("user", "approval", {"id": approval_id, "kind": item.kind, "status": status,
                                              "result": result})
        return result
