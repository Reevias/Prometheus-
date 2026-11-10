"""Obsidian: search, read and create notes in the owner's vault, and open notes in the Obsidian app.

A vault is a folder of Markdown files, so no plugin or key is needed. Notes are personal: answers that use them are
written by the local model only. New notes go through the undo journal, so they can be undone like any file.
"""
from __future__ import annotations

import json
import os
import re
import time
import webbrowser
from pathlib import Path
from urllib.parse import quote

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.config import load_settings
from prometheus.safety.paths import PathBlocked

NOTE_LIMIT = 5000              # notes looked at in one search
READ_CHARS = 200_000           # per note, when searching
SHOW_CHARS = 8000              # of a note that is read out
SKIP = {".obsidian", ".trash", ".git", "node_modules"}
TITLE = re.compile(r'[\\/:*?"<>|#^\[\]]')
open_uri = webbrowser.open     # replaced in tests


def detect_vaults() -> list[Path]:
    """Vaults Obsidian knows about on this laptop (from Obsidian's own settings file), newest first."""
    base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    for cfg in (base / "obsidian" / "obsidian.json", Path.home() / ".config" / "obsidian" / "obsidian.json"):
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        vaults = sorted((v for v in (data.get("vaults") or {}).values() if isinstance(v, dict) and v.get("path")),
                        key=lambda v: v.get("ts", 0), reverse=True)
        return [Path(v["path"]) for v in vaults if Path(v["path"]).is_dir()]
    return []


def safe_title(title: str) -> str:
    name = " ".join(TITLE.sub(" ", title or "").split()).strip(" .")[:120]
    return name or time.strftime("Note %Y-%m-%d %H%M")


class Vault:
    def __init__(self, folder: Path):
        self.folder = Path(folder)

    def notes(self):
        count = 0
        for root, dirs, files in os.walk(self.folder):
            dirs[:] = [d for d in dirs if d not in SKIP and not d.startswith(".")]
            for f in files:
                if f.lower().endswith(".md"):
                    count += 1
                    if count > NOTE_LIMIT:
                        return
                    yield Path(root) / f

    def find(self, name: str) -> Path | None:
        """A note by title or relative path ('Ideas', 'Projects/Prometheus', 'Ideas.md')."""
        want = name.strip().removesuffix(".md").replace("\\", "/").lower()
        best = None
        for p in self.notes():
            rel = p.relative_to(self.folder).with_suffix("").as_posix().lower()
            if rel == want:
                return p
            if best is None and (p.stem.lower() == want or rel.endswith("/" + want)):
                best = p
        return best

    def search(self, query: str, limit: int = 10) -> list[tuple[Path, str, int]]:
        words = [w for w in re.findall(r"\w+", query.lower()) if len(w) > 1]
        if not words:
            return []
        hits = []
        for p in self.notes():
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:READ_CHARS]
            except OSError:
                continue
            low, title = text.lower(), p.stem.lower()
            score = sum(3 for w in words if w in title) + sum(min(low.count(w), 5) for w in words)
            if score and all(w in low or w in title for w in words):
                i = min((low.find(w) for w in words if w in low), default=0)
                snippet = " ".join(text[max(0, i - 80): i + 160].split())
                hits.append((p, snippet, score))
        hits.sort(key=lambda h: (-h[2], -h[0].stat().st_mtime))
        return hits[:limit]

    def uri(self, note: Path) -> str:
        rel = note.relative_to(self.folder).as_posix()
        return f"obsidian://open?vault={quote(self.folder.name)}&file={quote(rel)}"


@register
class Obsidian(Agent):
    name = "obsidian"
    description = ("The owner's Obsidian vault (their notes). Actions: search (query), read (note = title or path), "
                   "create (title, content = Markdown, folder optional), recent (latest notes), open (note: opens it "
                   "in the Obsidian app). New notes can be undone on the History page.")
    tools = {"read_files", "move_files"}
    private_outputs = True
    required_inputs = ("action",)

    def __init__(self, vault: Path | None = None):
        self._vault = vault

    def _get_vault(self, ctx: AgentContext) -> Vault:
        folder = self._vault or (Path(load_settings().obsidian_vault) if load_settings().obsidian_vault else None)
        if folder is None:
            raise PathBlocked("Obsidian isn't connected yet. Connect your vault in Settings, under Obsidian.")
        ctx.policy.check_read(folder)
        return Vault(Path(folder).resolve())

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        action = str(inputs.get("action") or "").strip().lower()
        try:
            vault = self._get_vault(ctx)
            ctx.use(self.name, "read_files")
            if action == "search":
                return self._search(vault, str(inputs.get("query") or goal), ctx)
            if action == "read":
                return self._read(vault, str(inputs.get("note") or inputs.get("title") or ""), ctx)
            if action == "recent":
                notes = sorted(vault.notes(), key=lambda p: p.stat().st_mtime, reverse=True)[:15]
                for p in notes[:10]:
                    ctx.store.add_output(ctx.task_id, str(p), "found")
                return StepResult("done", "Latest notes:\n" + "\n".join(
                    f"- {p.relative_to(vault.folder).with_suffix('').as_posix()} "
                    f"({time.strftime('%Y-%m-%d', time.localtime(p.stat().st_mtime))})" for p in notes),
                    {"paths": [str(p) for p in notes]})
            if action == "open":
                note = vault.find(str(inputs.get("note") or inputs.get("title") or ""))
                if note is None:
                    return StepResult("failed", "I couldn't find that note in your vault.")
                open_uri(vault.uri(note))
                return StepResult("done", f"Opened {note.stem} in Obsidian.")
            if action == "create":
                ctx.use(self.name, "move_files")
                return self._create(vault, inputs, ctx)
        except PathBlocked as e:
            return StepResult("failed", str(e))
        return StepResult("failed", f"'{action}' is not an Obsidian action. I can: search, read, create, recent, open.")

    def _search(self, vault: Vault, query: str, ctx: AgentContext) -> StepResult:
        hits = vault.search(query)
        if not hits:
            return StepResult("done", f"No note in your vault mentions: {query}", {"paths": []})
        for p, _, _ in hits:
            ctx.store.add_output(ctx.task_id, str(p), "found")
        lines = [f"- {p.relative_to(vault.folder).with_suffix('').as_posix()}: …{s}…" for p, s, _ in hits]
        return StepResult("done", f"{len(hits)} note(s) about '{query}':\n" + untrusted("\n".join(lines), "your notes"),
                          {"paths": [str(p) for p, _, _ in hits]})

    def _read(self, vault: Vault, name: str, ctx: AgentContext) -> StepResult:
        note = vault.find(name)
        if note is None:
            return StepResult("failed", f"I couldn't find a note called '{name}'.")
        ctx.store.add_output(ctx.task_id, str(note), "found")
        text = note.read_text(encoding="utf-8", errors="replace")[:SHOW_CHARS]
        return StepResult("done", f"{note.stem}:\n" + untrusted(text, note.name), {"paths": [str(note)]})

    def _create(self, vault: Vault, inputs: dict, ctx: AgentContext) -> StepResult:
        title = safe_title(str(inputs.get("title") or ""))
        content = str(inputs.get("content") or "")
        if len(content) > 1_000_000:
            return StepResult("failed", "That is too much text for one note.")
        folder = str(inputs.get("folder") or "").strip().strip("/\\")
        target_dir = vault.folder
        for part in Path(folder).parts if folder else ():
            if part in ("..", ".") or part.startswith("."):
                return StepResult("failed", "That folder name isn't allowed in the vault.")
            target_dir = target_dir / safe_title(part)
        root = ctx.policy.root_for(vault.folder)
        if root is None:
            return StepResult("failed", "The vault is outside the places Prometheus may use.")
        run = ctx.files.journal.start(root)
        try:
            res = ctx.files.create(run, target_dir / f"{title}.md", content.encode("utf-8"), root)
        finally:
            ctx.files.journal.finish(run)
        if res.status != "done":
            return StepResult("failed", res.detail)
        ctx.store.add_output(ctx.task_id, str(res.new_path), "created")
        return StepResult("done", f"Created the note {res.new_path.stem} in your vault. You can undo it on the History "
                                  "page.", {"paths": [str(res.new_path)]})
