"""Approval queue: risky actions wait for the user's Approve / Reject."""
from __future__ import annotations

from typing import Callable

from prometheus.memory.store import Store


class Approvals:
    def __init__(self, store: Store):
        self.store = store
        self._handlers: dict[str, Callable[[dict], str]] = {}

    def register(self, kind: str, handler: Callable[[dict], str]) -> None:
        self._handlers[kind] = handler

    def request(self, kind: str, summary: str, payload: dict) -> int:
        approval_id = self.store.add_approval(kind, summary, payload)
        self.store.audit("approvals", "requested", {"id": approval_id, "kind": kind, "summary": summary})
        return approval_id

    def resolve(self, approval_id: int, approved: bool) -> str:
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
                result, status = handler(item.payload), "approved"
            except Exception as e:  # handler failures are recorded, never crash the app
                result, status = f"Failed: {e}", "failed"
            self.store.set_approval_status(approval_id, status)
        self.store.audit("user", "approval", {"id": approval_id, "kind": item.kind, "status": status,
                                              "result": result})
        return result
