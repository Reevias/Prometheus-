"""What the sidebar lists (laptop check, 20 Nov 2026): projects with their newest chats, and the newest other chats.

A chat's "activity" is its newest message, or when it started if it has none yet."""
from __future__ import annotations

from prometheus import projects as pj

RECENTS = 15
PER_PROJECT = 8


def _flags(store) -> dict:
    flags = store.kv_get("chat_flags", {})
    flags = flags if isinstance(flags, dict) else {}
    return {k: {i for i in flags.get(k, []) if isinstance(i, int)} for k in ("pinned", "archived")}


def sidebar_data(store, open_chat: int = 0) -> dict:
    pj.ready(store)
    rows = store._rows(
        "SELECT c.id, c.title, c.project_id AS project, MAX(c.created, COALESCE(MAX(m.created), 0)) AS active "
        "FROM chats c LEFT JOIN messages m ON m.chat_id = c.id GROUP BY c.id ORDER BY active DESC, c.id DESC")
    flags = _flags(store)
    live = {p["id"]: p for p in pj.all_projects(store)}
    by_project: dict[int, list[dict]] = {pid: [] for pid in live}
    loose = []
    for r in rows:
        chat = {"id": r["id"], "title": r["title"] or "New chat", "pinned": r["id"] in flags["pinned"]}
        if r["project"] in live:
            by_project[r["project"]].append(chat)
        elif r["id"] not in flags["archived"]:
            loose.append(chat)
    loose.sort(key=lambda c: not c["pinned"])                  # pinned first, otherwise newest first (stable)
    projects = []
    for pid in sorted(live, key=lambda p: -max([pj.last_activity(store, p)] or [0])):
        chats = by_project[pid]
        projects.append({"id": pid, "name": live[pid]["name"], "chats": chats[:PER_PROJECT],
                         "more": len(chats) > PER_PROJECT})
    return {"projects": projects, "project_options": [[p["id"], p["name"]] for p in projects],
            "recents": loose[:RECENTS], "more_recents": len(loose) > RECENTS, "open_chat": int(open_chat or 0)}
