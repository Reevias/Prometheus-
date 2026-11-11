"""'Undo that': reverses the last thing Prometheus did, within an hour, after a yes.

File changes (moves, renames, new files and notes, photo sorting) come from the History journal; everything else
is written here when it happens: reminders and goals, calendar events, saved facts, model changes. Sent email
can't be unsent, so that is said plainly.
"""
from __future__ import annotations

import json
import time

WINDOW = 3600
SCHEMA = ("CREATE TABLE IF NOT EXISTS undo_log (id INTEGER PRIMARY KEY, created REAL NOT NULL, kind TEXT NOT NULL, "
          "summary TEXT NOT NULL, data TEXT NOT NULL, undone INTEGER NOT NULL DEFAULT 0)")
SERVICES = None          # set by the app


def _ready(store) -> None:
    with store._lock:
        store._db.execute(SCHEMA)
        store._db.commit()


def record(store, kind: str, summary: str, data: dict) -> None:
    try:
        _ready(store)
        store._write("INSERT INTO undo_log (created, kind, summary, data) VALUES (?, ?, ?, ?)",
                     (time.time(), kind, summary[:300], json.dumps(data)))
    except Exception:
        pass            # never let the undo record stop the real action


def _describe_run(run: dict, journal) -> str:
    try:
        entries = journal.load(run["id"])["entries"]
    except Exception:
        entries = []
    if not entries:
        return f"{run['count']} file change(s)"
    first = entries[0]
    verb = {"move": "moved", "rename": "renamed", "copy": "copied", "create": "made", "mkdir": "made the folder",
            "recycle": "sent to the Recycle Bin"}.get(first.get("op", ""), first.get("op", "changed"))
    name = (first.get("to") or first.get("from") or first.get("path") or "").split("/")[-1].split("\\")[-1]
    more = f" and {len(entries) - 1} more" if len(entries) > 1 else ""
    return f"{verb} {name}{more}"


def last(services, now: float | None = None) -> dict | None:
    """The newest thing that can be undone (made in the last hour and not undone yet)."""
    now = time.time() if now is None else now
    store = services.store
    _ready(store)
    found = []
    rows = store._rows("SELECT * FROM undo_log WHERE undone = 0 AND created > ? ORDER BY id DESC LIMIT 1",
                       (now - WINDOW,))
    if rows:
        r = rows[0]
        found.append({"source": "log", "id": r["id"], "kind": r["kind"], "summary": r["summary"],
                      "created": r["created"]})
    journal = getattr(services, "journal", None)
    if journal is not None:
        runs = [r for r in journal.runs() if r.get("finished") and not r.get("undone") and r["count"]
                and r["created"] > now - WINDOW and not r.get("active")]
        if runs:
            run = max(runs, key=lambda r: r["created"])
            found.append({"source": "files", "id": run["id"], "kind": "files",
                          "summary": _describe_run(run, journal), "created": run["created"]})
    return max(found, key=lambda f: f["created"]) if found else None


def perform(services, item: dict) -> str:
    store = services.store
    if item["source"] == "files":
        report = services.files.undo_run(item["id"])
        return f"Undone: {item['summary']} ({report.restored} restored)." + (
            f" {len(report.problems)} couldn't be put back." if getattr(report, "problems", None) else "")
    rows = store._rows("SELECT * FROM undo_log WHERE id = ? AND undone = 0", (item["id"],))
    if not rows:
        return "That was already undone."
    kind, data = rows[0]["kind"], json.loads(rows[0]["data"])
    if kind == "email":
        return "Sent email can't be unsent. You could send a follow-up email instead."
    if kind == "reminder":
        store.cancel_reminder(int(data["id"]))
    elif kind == "fact":
        for fid in data.get("ids", []):
            services.orchestrator.facts.forget(int(fid))
    elif kind == "google_event":
        from prometheus.agents.google_agent import google

        google().delete_event(str(data["id"]))
    elif kind == "setting":
        from prometheus.config import save_settings

        setattr(services.settings, data["field"], data["old"])
        save_settings(services.settings)
        live = services.router.providers.get(data.get("provider", ""))
        if live is not None and data.get("provider"):
            live.model = data["old"]
    else:
        return "I don't know how to undo that."
    store._write("UPDATE undo_log SET undone = 1 WHERE id = ?", (item["id"],))
    return f"Undone: {rows[0]['summary']}."


def handler(payload: dict) -> str:
    return perform(SERVICES, payload)
