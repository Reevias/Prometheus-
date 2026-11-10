"""Every chat, kept as readable files in the "Prometheus memory" folder and split by topic.

Each chat is cut into segments: a run of messages about one topic (the planner names the topic of each request).
A segment is saved as chunks, so a later question fetches only the few passages it needs instead of whole chats.
Segments with the same topic are linked across chats: topics/weather.md lists every chat that asked about the
weather.

Folder layout:
    Prometheus memory/
        index.md               every topic and the latest chats
        chats/<date> <title> (<id>).md
        topics/<topic>.md      links to every chat part about that topic
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from prometheus.memory.store import Store

FOLDER_NAME = "Prometheus memory"
GENERAL = "general"
MAX_TOPIC_WORDS = 4
MAX_TOPIC_CHARS = 40
SAFE = re.compile(r"[^A-Za-z0-9 ._()-]+")
STOP = frozenset("a an the of and or for to in on about my your with".split())


def default_folder(home: Path) -> Path:
    """In the home folder, not Documents: Documents is often synced to OneDrive, and chats can hold private text."""
    return Path(home) / FOLDER_NAME


def _stem(word: str) -> str:
    for end in ("ies", "es", "s"):
        if len(word) > len(end) + 2 and word.endswith(end):
            return word[: -len(end)] + ("y" if end == "ies" else "")
    return word


def _key(topic: str) -> frozenset[str]:
    return frozenset(_stem(w) for w in topic.split() if w not in STOP)


def normalize_topic(raw: str, known: list[str] | tuple = ()) -> str:
    """A short lowercase label. Reuses a known topic with the same words ("Weather forecasts" → "weather forecast")."""
    words = re.sub(r"[^a-z0-9 -]+", " ", (raw or "").lower().replace("_", " ")).split()
    topic = " ".join(words[:MAX_TOPIC_WORDS])[:MAX_TOPIC_CHARS].strip(" -")
    if not topic:
        return ""
    key = _key(topic)
    for name in known:
        if key and _key(name) == key:
            return name
    return topic


def _safe_name(text: str, limit: int = 60) -> str:
    name = SAFE.sub(" ", text).strip(" .")
    return " ".join(name.split())[:limit].strip(" .") or "chat"


def _date(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


class ChatArchive:
    def __init__(self, store: Store, folder: Path):
        self.store = store
        self.folder = Path(folder)
        self._lock = threading.Lock()

    # -- splitting a chat by topic --------------------------------------------------
    def split(self, chat_id: int) -> list[tuple[str, list]]:
        """Runs of messages that share a topic. A message without a topic belongs with the one before it."""
        runs: list[tuple[str, list]] = []
        current = ""
        for m in self.store.messages(chat_id):
            if m.role not in ("user", "assistant"):
                continue
            topic = m.topic or current or GENERAL
            if runs and topic == runs[-1][0]:
                runs[-1][1].append(m)
            else:
                runs.append((topic, [m]))
            current = topic
        return runs

    @staticmethod
    def segment_text(topic: str, title: str, msgs: list) -> str:
        lines = [f"Topic: {topic}. Chat: {title}. Date: {_date(msgs[0].created)}."]
        for m in msgs:
            who = "You" if m.role == "user" else "Prometheus"
            lines.append(f"{who}: {m.text.strip()}")
        return "\n\n".join(lines)

    # -- keeping memory and files up to date -------------------------------------------
    def sync(self, chat_id: int) -> None:
        with self._lock:
            title = self.store.chat_title(chat_id)
            runs = self.split(chat_id)
            prefix = f"chat:{chat_id}:"
            old_keys = self.store.document_keys(prefix)
            before = {s["topic"] for s in self.store.segments(chat_id=chat_id)}
            segments, keep = [], set()
            for topic, msgs in runs:
                key = f"{prefix}{msgs[0].id}"
                keep.add(key)
                doc_id = self.store.add_document(key, f"{topic} · {title}", f"chat: {title}",
                                                 self.segment_text(topic, title, msgs),
                                                 private=any(m.private for m in msgs))
                segments.append((topic, msgs[0].id, msgs[-1].id, doc_id, msgs[0].created))
            for key, doc_id in old_keys.items():
                if key not in keep:
                    self.store.delete_document(doc_id)
            self.store.set_segments(chat_id, segments)
            try:
                self._write_chat(chat_id, title, runs)
                for topic in before | {t for t, _ in runs}:
                    self._write_topic(topic)
                self._write_index()
            except OSError:      # a locked or read-only file never stops the chat
                pass

    def sync_all(self) -> None:
        """Backfill: archive chats that have no segments yet (for example chats from before this version)."""
        done = {s["chat_id"] for s in self.store.segments()}
        for chat_id, _ in self.store.chats():
            if chat_id not in done and self.store.messages(chat_id):
                self.sync(chat_id)

    # -- files ------------------------------------------------------------------
    def chat_file(self, chat_id: int, title: str) -> Path:
        name = f"{_date(self.store.chat_created(chat_id))} {_safe_name(title)} ({chat_id}).md"
        return self.folder / "chats" / name

    def topic_file(self, topic: str) -> Path:
        return self.folder / "topics" / f"{_safe_name(topic, 40)}.md"

    def _write_chat(self, chat_id: int, title: str, runs: list[tuple[str, list]]) -> None:
        path = self.chat_file(chat_id, title)
        for old in (self.folder / "chats").glob(f"* ({chat_id}).md"):
            if old != path:          # the chat was renamed
                old.unlink(missing_ok=True)
        out = [f"# {title}", "", f"Started {_date(self.store.chat_created(chat_id))}. Chat number {chat_id}.", ""]
        for topic, msgs in runs:
            out += [f"## {topic}", f"[All chats about {topic}](../topics/{self.topic_file(topic).name.replace(' ', '%20')})", ""]
            for m in msgs:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(m.created))
                who = "You" if m.role == "user" else "Prometheus"
                out += [f"**{who}** · {when}", "", m.text.strip(), ""]
        _write(path, "\n".join(out))

    def _write_topic(self, topic: str) -> None:
        parts = self.store.segments(topic=topic)
        path = self.topic_file(topic)
        if not parts:
            path.unlink(missing_ok=True)
            return
        out = [f"# {topic}", "", f"{len({p['chat_id'] for p in parts})} chats talked about this.", ""]
        for p in parts:
            first = next((m for m in self.store.messages(p["chat_id"])
                          if m.id >= p["first_msg"] and m.role == "user"), None)
            asked = (first.text.strip().splitlines() or [""])[0][:120] if first else ""
            link = self.chat_file(p["chat_id"], p["chat_title"]).name.replace(" ", "%20")
            out.append(f"- {_date(p['started'])} · [{p['chat_title']}](../chats/{link}) · {asked}")
        _write(path, "\n".join(out) + "\n")

    def _write_index(self) -> None:
        out = ["# Prometheus memory", "", "Every chat, split by topic. Chats about the same topic are linked.", "",
               "## Topics", ""]
        for t in self.store.topics():
            link = self.topic_file(t["topic"]).name.replace(" ", "%20")
            out.append(f"- [{t['topic']}](topics/{link}) · {t['chats']} chats · last {_date(t['last'])}")
        out += ["", "## Latest chats", ""]
        for chat_id, title in self.store.chats()[:50]:
            path = self.chat_file(chat_id, title)
            if path.exists():
                out.append(f"- [{title}](chats/{path.name.replace(' ', '%20')})")
        _write(self.folder / "index.md", "\n".join(out) + "\n")
