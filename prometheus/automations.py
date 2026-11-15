"""Automations you build (M2.3): "when this happens, do that", plus the ask-first switch for timed goals.

Timed automations (once, every day, every week, weekdays) are reminders and standing goals (store.reminders); this
module adds the rules that wait for something to happen: a new file in a folder, the battery running low, the disk
filling up, or the CPU staying busy. The watchers notice these and call fire().

Each automation either reminds you (a message and a Windows notification) or does something (a task, like a typed
request). With "ask me first" on, it asks on the Approvals page and runs only after your yes. Without it, it runs, and
risky steps (deleting, sending, paying) still ask, as every task does. Running fully without asking is M10.
"""
from __future__ import annotations

import json
import time

SCHEMA = ("CREATE TABLE IF NOT EXISTS automation_rules (id INTEGER PRIMARY KEY, name TEXT NOT NULL, event TEXT NOT NULL, "
          "folder TEXT NOT NULL DEFAULT '', action TEXT NOT NULL, text TEXT NOT NULL, ask_first INTEGER NOT NULL DEFAULT 1, "
          "enabled INTEGER NOT NULL DEFAULT 1, created REAL NOT NULL, last_run REAL NOT NULL DEFAULT 0, "
          "last_result TEXT NOT NULL DEFAULT '')")
EVENTS = {"new_file": "A new file appears in a folder", "battery": "The battery runs low (under 20%)",
          "disk": "The main drive is almost full (over 90%)", "cpu": "The CPU stays busy (over 90% for 5 minutes)"}
ACTIONS = {"remind": "Remind me", "do": "Do something (a task)"}
CHAT = "Automations"
APPROVAL_KIND = "run_automation"
MAX_RULES = 50


def ready(store) -> None:
    with store._lock:
        store._db.execute(SCHEMA)
        cols = {r["name"] for r in store._db.execute("PRAGMA table_info(reminders)")}
        if "ask_first" not in cols:
            store._db.execute("ALTER TABLE reminders ADD COLUMN ask_first INTEGER NOT NULL DEFAULT 0")
        store._db.commit()


def clean(text: str, limit: int = 300) -> str:
    return " ".join((text or "").split())[:limit]


def add_rule(store, name: str, event: str, action: str, text: str, folder: str = "", ask_first: bool = True) -> int:
    ready(store)
    if event not in EVENTS:
        raise ValueError("Choose what it waits for.")
    if action not in ACTIONS:
        raise ValueError("Choose whether it reminds you or does something.")
    text, name = clean(text), clean(name, 80)
    if not text:
        raise ValueError("Say what it should remind you of, or do.")
    if event == "new_file" and not folder.strip():
        raise ValueError("Choose the folder to watch.")
    if len(rules(store)) >= MAX_RULES:
        raise ValueError(f"Up to {MAX_RULES} automations of this kind: delete one first.")
    return store._write("INSERT INTO automation_rules (name, event, folder, action, text, ask_first, created) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (name or text[:60], event, folder.strip() if event == "new_file" else "", action, text,
                         1 if ask_first else 0, time.time()))


def rules(store, event: str | None = None, enabled_only: bool = False) -> list[dict]:
    ready(store)
    where, args = [], []
    if event:
        where.append("event = ?")
        args.append(event)
    if enabled_only:
        where.append("enabled = 1")
    sql = "SELECT * FROM automation_rules" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id"
    return [dict(r) for r in store._rows(sql, tuple(args))]


def rule(store, rule_id: int) -> dict | None:
    ready(store)
    rows = store._rows("SELECT * FROM automation_rules WHERE id = ?", (rule_id,))
    return dict(rows[0]) if rows else None


def set_rule(store, rule_id: int, **fields) -> bool:
    allowed = {k: v for k, v in fields.items() if k in ("enabled", "ask_first", "text", "name")}
    if not allowed:
        return False
    sets = ", ".join(f"{k} = ?" for k in allowed)
    with store._lock:
        cur = store._db.execute(f"UPDATE automation_rules SET {sets} WHERE id = ?", (*allowed.values(), rule_id))
        store._db.commit()
    return cur.rowcount > 0


def delete_rule(store, rule_id: int) -> bool:
    with store._lock:
        cur = store._db.execute("DELETE FROM automation_rules WHERE id = ?", (rule_id,))
        store._db.commit()
    return cur.rowcount > 0


def chat_id(store) -> int:
    for cid, title in store.chats():
        if title == CHAT:
            return cid
    cid = store.create_chat(CHAT)
    store.rename_chat(cid, CHAT, by="user")
    return cid


def what_happened(event: str, detail: dict) -> str:
    if event == "new_file":
        names = [str(p).replace("\\", "/").split("/")[-1] for p in detail.get("files", [])][:5]
        return f"new in {str(detail.get('folder', '')).replace(chr(92), '/').split('/')[-1]}: {', '.join(names)}"
    return {"battery": "the battery is low", "disk": "the disk is almost full", "cpu": "the CPU has been busy"}.get(event, event)


def task_text(r: dict, detail: dict) -> str:
    """The request a "do" automation runs. Names of new files are outside text: marked so they're never obeyed."""
    from prometheus.agents.base import untrusted

    text = r["text"]
    if r["event"] == "new_file" and detail.get("files"):
        text += "\n" + untrusted("\n".join(str(p) for p in detail["files"][:20]), "names of the new files")
    return text


def run(services, r: dict, detail: dict | None = None, by_hand: bool = False) -> str:
    """Do what an automation says, now (or ask first). Returns what happened, in plain words."""
    detail = detail or {}
    store = services.store
    cid = chat_id(store)
    why = "you pressed Run now" if by_hand else what_happened(r["event"], detail)
    if r["action"] == "remind":
        store.add_message(cid, "assistant", f"⏰ {r['text']} ({why})", "automation")
        if getattr(services, "notify", None):
            try:
                services.notify("automation", r["text"])
            except Exception:
                pass
        said = "Reminded you."
    elif r["ask_first"] and not by_hand:
        services.approvals.request(APPROVAL_KIND, f"Run your automation “{r['name']}”? ({why})",
                                   {"rule": r["id"], "chat": cid, "text": task_text(r, detail)})
        said = "Asked you first (see Approvals)."
    elif cid in services.running:
        said = "Skipped: the Automations chat was busy."
    else:
        store.add_message(cid, "assistant", f"Running your automation “{r['name']}” ({why}).", "automation")
        services.start_task(cid, task_text(r, detail))
        said = "Started it (see Tasks)."
    store._write("UPDATE automation_rules SET last_run = ?, last_result = ? WHERE id = ?", (time.time(), said, r["id"]))
    store.audit("automation", "fired", {"rule": r["id"], "event": r["event"], "by_hand": by_hand, "result": said})
    return said


def fire(services, event: str, detail: dict | None = None) -> int:
    """Something happened (from the watchers): run each switched-on automation waiting for it. Returns how many."""
    detail = detail or {}
    n = 0
    for r in rules(services.store, event, enabled_only=True):
        if event == "new_file" and _norm(r["folder"]) != _norm(str(detail.get("folder", ""))):
            continue
        try:
            run(services, r, detail)
            n += 1
        except Exception:
            continue
    return n


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


def approved(services, payload: dict) -> str:
    """After your yes on the Approvals page: run the automation's task in the Automations chat."""
    cid = int(payload.get("chat") or chat_id(services.store))
    if cid in services.running:
        return "The Automations chat is busy right now; run it again from Automations in a moment."
    services.start_task(cid, str(payload.get("text") or ""))
    return "Started it (see Tasks)."


def goal_needs_yes(services, r: dict) -> bool:
    """A timed goal with "ask me first": ask on the Approvals page instead of starting. True when it asked."""
    if not r.get("ask_first") or r.get("kind") != "goal":
        return False
    services.approvals.request(APPROVAL_KIND, f"Run your scheduled goal “{clean(r['text'], 80)}”?",
                               {"chat": r["chat_id"], "text": r["text"]})
    return True


def watched_folders(services) -> list[str]:
    """Folders the new-file automations wait on (the watchers look at these as well as their own)."""
    return sorted({r["folder"] for r in rules(services.store, "new_file", enabled_only=True) if r["folder"]})


def describe(r: dict) -> dict:
    return {**r, "event_label": EVENTS.get(r["event"], r["event"]), "action_label": ACTIONS.get(r["action"], r["action"]),
            "json": json.dumps({"id": r["id"]})}
