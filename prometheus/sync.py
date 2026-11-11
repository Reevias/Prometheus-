"""Keeping the laptop and the always-on cloud copy the same.

Every 10 minutes while the laptop is on, it sends the cloud copy what changed and gets back what changed there,
over the private Tailscale link, with a shared sync key. Rows are matched by what they are, not by number
(a chat by its title and start time, a message by its chat, time and text, a flashcard by deck and question…), so
nothing is doubled and nothing is lost: chats from the phone appear on the laptop, and the other way round.

Synced: chats and messages, memories, people and facts, flashcards (the most-practised copy wins), spending,
deadlines, reminders and standing goals (a reminder that rang on one side is marked rung on the other).
While the laptop is on, the cloud copy stays quiet (no reminders, alerts or briefings of its own), so nothing
arrives twice; when the laptop has been away for 15 minutes, the cloud takes over.
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time

TOKEN_KEY = "SYNC_TOKEN"
EVERY = 600
AWAY = 900
LIMIT = 5000


def _h(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def _tables(store) -> set[str]:
    return {r["name"] for r in store._rows("SELECT name FROM sqlite_master WHERE type = 'table'")}


def export(store, since: float) -> dict:
    """Everything made or changed after `since` (small tables are sent whole; their keys make that harmless)."""
    have = _tables(store)
    chats = {r["id"]: (r["title"], r["created"]) for r in store._rows("SELECT id, title, created FROM chats")}
    out = {"chats": [{"title": t, "created": c} for t, c in chats.values()],
           "messages": [{"chat": list(chats.get(r["chat_id"], ("", 0))), "role": r["role"], "text": r["text"],
                         "created": r["created"], "model": r["model"], "private": r["private"], "topic": r["topic"]}
                        for r in store._rows("SELECT * FROM messages WHERE created > ? ORDER BY id LIMIT ?",
                                             (since, LIMIT)) if r["chat_id"] in chats],
           "memories": [dict(r) for r in store._rows("SELECT text, created, source FROM memories")],
           "reminders": [{"chat": list(chats.get(r["chat_id"], ("", 0))), "text": r["text"], "due": r["due"],
                          "repeat": r["repeat"], "state": r["state"], "created": r["created"], "kind": r["kind"]}
                         for r in store._rows("SELECT * FROM reminders")]}
    if "facts" in have:
        out["facts"] = [dict(r) for r in store._rows("SELECT subject, attribute, value, created, until, source "
                                                     "FROM facts")]
    if "cards" in have:
        out["cards"] = [dict(r) for r in store._rows("SELECT deck, question, answer, source, ease, interval, due, "
                                                     "reps, lapses, created FROM cards")]
    if "spending" in have:
        out["spending"] = [dict(r) for r in store._rows("SELECT created, amount, kind, merchant, category, source, "
                                                        "ref FROM spending")]
    if "assignments" in have:
        out["assignments"] = [dict(r) for r in store._rows("SELECT title, due, source, ref, done, created "
                                                           "FROM assignments")]
    return out


def merge(store, data: dict) -> dict:
    """Add what this side doesn't have yet. Returns counts of what was new."""
    from prometheus.memory.facts import Facts
    from prometheus.study import Study

    Facts(store)                       # make sure the tables exist on this side
    Study(store)
    from prometheus.assignments import Assignments
    from prometheus.money import Money

    Money(store)
    Assignments(store)
    n = {k: 0 for k in ("chats", "messages", "memories", "facts", "cards", "spending", "assignments", "reminders")}
    chat_ids = {(r["title"], round(r["created"], 3)): r["id"] for r in store._rows("SELECT id, title, created FROM chats")}

    def chat_id(key) -> int | None:
        title, created = key[0], round(float(key[1] or 0), 3)
        if not title:
            return None
        if (title, created) not in chat_ids:
            chat_ids[(title, created)] = store._write("INSERT INTO chats (title, created) VALUES (?, ?)",
                                                      (title, created))
            n["chats"] += 1
        return chat_ids[(title, created)]

    for c in data.get("chats", []):
        chat_id([c["title"], c["created"]])
    seen = {(r["chat_id"], round(r["created"], 3), _h(r["text"]))
            for r in store._rows("SELECT chat_id, created, text FROM messages WHERE created > ?",
                                 (min([m["created"] for m in data.get("messages", [])] or [time.time()]) - 1,))}
    for m in data.get("messages", []):
        cid = chat_id(m["chat"])
        if cid is None or (cid, round(m["created"], 3), _h(m["text"])) in seen:
            continue
        mid = store._write("INSERT INTO messages (chat_id, role, text, created, model, private, topic) VALUES "
                           "(?, ?, ?, ?, ?, ?, ?)", (cid, m["role"], m["text"], m["created"], m.get("model", ""),
                                                     int(m.get("private", 0)), m.get("topic", "")))
        store._index("message", mid, m["text"])
        n["messages"] += 1
    known = {r["text"] for r in store._rows("SELECT text FROM memories")}
    for m in data.get("memories", []):
        if m["text"] not in known:
            mid = store._write("INSERT INTO memories (text, created, source) VALUES (?, ?, ?)",
                               (m["text"], m["created"], m.get("source", "")))
            store._index("memory", mid, m["text"])
            known.add(m["text"])
            n["memories"] += 1
    facts = {(r["subject"].lower(), r["attribute"], r["value"], round(r["created"], 3)): r
             for r in store._rows("SELECT * FROM facts")}
    for f in data.get("facts", []):
        key = (f["subject"].lower(), f["attribute"], f["value"], round(f["created"], 3))
        if key not in facts:
            store._write("INSERT INTO facts (subject, attribute, value, created, until, source) VALUES "
                         "(?, ?, ?, ?, ?, ?)", (f["subject"], f["attribute"], f["value"], f["created"], f["until"],
                                                f.get("source", "")))
            n["facts"] += 1
        elif f["until"] and not facts[key]["until"]:
            store._write("UPDATE facts SET until = ? WHERE id = ?", (f["until"], facts[key]["id"]))
    cards = {(r["deck"], r["question"].lower()): r for r in store._rows("SELECT * FROM cards")}
    for c in data.get("cards", []):
        key = (c["deck"], c["question"].lower())
        mine = cards.get(key)
        if mine is None:
            store._write("INSERT INTO cards (deck, question, answer, source, ease, interval, due, reps, lapses, "
                         "created) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         tuple(c[k] for k in ("deck", "question", "answer", "source", "ease", "interval", "due",
                                              "reps", "lapses", "created")))
            n["cards"] += 1
        elif (c["reps"] + c["lapses"], c["due"]) > (mine["reps"] + mine["lapses"], mine["due"]):
            store._write("UPDATE cards SET ease = ?, interval = ?, due = ?, reps = ?, lapses = ? WHERE id = ?",
                         (c["ease"], c["interval"], c["due"], c["reps"], c["lapses"], mine["id"]))
    for s in data.get("spending", []):
        try:
            store._write("INSERT INTO spending (created, amount, kind, merchant, category, source, ref) VALUES "
                         "(?, ?, ?, ?, ?, ?, ?)", tuple(s[k] for k in ("created", "amount", "kind", "merchant",
                                                                        "category", "source", "ref")))
            n["spending"] += 1
        except Exception:
            pass                      # the same ref is already here
    for a in data.get("assignments", []):
        rows = store._rows("SELECT id, done FROM assignments WHERE ref = ?", (a["ref"],))
        if not rows:
            store._write("INSERT INTO assignments (title, due, source, ref, done, created) VALUES (?, ?, ?, ?, ?, ?)",
                         tuple(a[k] for k in ("title", "due", "source", "ref", "done", "created")))
            n["assignments"] += 1
        elif a["done"] and not rows[0]["done"]:
            store._write("UPDATE assignments SET done = 1 WHERE id = ?", (rows[0]["id"],))
    mine = {(r["text"], round(r["created"], 3)): r for r in store._rows("SELECT * FROM reminders")}
    for r in data.get("reminders", []):
        key = (r["text"], round(r["created"], 3))
        if key not in mine:
            cid = chat_id(r["chat"]) or 0
            store._write("INSERT INTO reminders (chat_id, text, due, repeat, state, created, kind) VALUES "
                         "(?, ?, ?, ?, ?, ?, ?)", (cid, r["text"], r["due"], r["repeat"], r["state"], r["created"],
                                                   r.get("kind", "reminder")))
            n["reminders"] += 1
        else:
            m = mine[key]
            # it rang (or was cancelled) on the other side: the same here, so it doesn't ring twice
            if r["state"] != "waiting" and m["state"] == "waiting":
                store._write("UPDATE reminders SET state = ? WHERE id = ?", (r["state"], m["id"]))
            elif r["state"] == "waiting" and m["state"] == "waiting" and r["due"] > m["due"]:
                store._write("UPDATE reminders SET due = ? WHERE id = ?", (r["due"], m["id"]))
    return n


def token(create: bool = True) -> str:
    from prometheus.config import get_secret, set_secret

    t = get_secret(TOKEN_KEY) or ""
    if not t and create:
        t = secrets.token_urlsafe(32)
        set_secret(TOKEN_KEY, t)
    return t


def laptop_away(store, clock=time.time) -> bool:
    """On the cloud copy: True when the laptop hasn't synced for 15 minutes (so the cloud should speak up)."""
    return clock() - float(store.kv_get("laptop_seen", 0) or 0) > AWAY


def quiet(services) -> bool:
    """True when this copy should keep its reminders, alerts and briefings to itself."""
    s = getattr(services, "settings", None)
    return getattr(s, "role", "laptop") == "cloud" and not laptop_away(services.store)


class SyncClock:
    """On the laptop: every 10 minutes, swap changes with the cloud copy."""

    def __init__(self, services, http=None, clock=time.time):
        self.services, self.clock = services, clock
        self.http = http
        self._stop = threading.Event()
        self.status = ""

    def once(self) -> str:
        import httpx

        s, store = self.services.settings, self.services.store
        url = (getattr(s, "cloud_url", "") or "").rstrip("/")
        if not url or not token(create=False):
            return ""
        since = float(store.kv_get("sync_since", 0) or 0)
        started = self.clock()
        http = self.http or httpx.Client(timeout=60)
        try:
            resp = http.post(f"{url}/api/sync", json={"since": since, "data": export(store, since)},
                             headers={"Authorization": f"Bearer {token(create=False)}"})
        except Exception as e:
            self.status = f"Couldn't reach the cloud copy ({type(e).__name__})."
            store.kv_set("sync_status", self.status)
            return self.status
        if resp.status_code != 200:
            self.status = f"The cloud copy refused the sync ({resp.status_code}): check the sync key."
            store.kv_set("sync_status", self.status)
            return self.status
        got = merge(store, resp.json().get("data", {}))
        store.kv_set("sync_since", started - 5)
        new = sum(got.values())
        self.status = time.strftime("Synced %H:%M", time.localtime(self.clock())) + (f" ({new} new here)" if new else "")
        store.kv_set("sync_status", self.status)
        return self.status

    def loop(self) -> None:
        while not self._stop.wait(EVERY):
            try:
                self.once()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-sync", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
