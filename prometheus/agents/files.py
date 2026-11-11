"""Finds and handles files anywhere Prometheus may go: the user folder plus any drives or folders added in Settings.

Reading (find, list, info) happens straight away. Copying, moving, renaming and making folders happen straight away
too, and every one can be undone on the History page. Sending more than a few files to the Recycle Bin waits for
the owner's approval. Nothing is ever deleted for good, and system folders, browser data, passwords and
Prometheus's own files are always out of reach.
"""
from __future__ import annotations

import fnmatch
import os
import re
import time
from datetime import datetime
from pathlib import Path

from prometheus.actions.files import FileActions
from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.agents.file_sorter import TYPES, is_system_file
from prometheus.safety.paths import PathBlocked, PathPolicy

READ_ACTIONS = {"find", "search_text", "list", "info"}
CHANGE_ACTIONS = {"copy", "move", "rename", "recycle", "make_folder", "create"}
MAX_NEW_FILE = 2_000_000
TEXT_TYPES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".html", ".htm", ".css", ".xml", ".yaml", ".yml",
              ".ini", ".toml", ".log", ".py", ".java", ".c", ".cpp", ".h", ".cs", ".go", ".rs", ".ts", ".sql", ".tex",
              ".srt", ".rtf"}
# Types Windows runs when they are double-clicked: Prometheus never writes them.
RUNNABLE = {".exe", ".bat", ".cmd", ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".msi", ".msp",
            ".scr", ".com", ".lnk", ".url", ".reg", ".hta", ".cpl", ".pif", ".jar", ".appx", ".msix", ".sh", ".dll",
            ".sys", ".inf", ".application", ".gadget", ".scf", ".library-ms"}
FIND_LIMIT = 50
OUTPUTS_SHOWN = 10              # found files listed under the chat
LIST_LIMIT = 200
SCAN_ENTRIES = 300_000          # stop a search after this many files and folders...
SCAN_SECONDS = 25.0             # ...or this long, and say so
RECYCLE_WITHOUT_ASKING = 10
SKIP_DIRS = {"node_modules", ".git", "__pycache__", ".venv", "venv", "site-packages", "$recycle.bin",
             "system volume information", "appdata", ".cache", "cache", "temp", "tmp"}
KIND_ALIASES = {"images": "Images", "photos": "Images", "pictures": "Images", "documents": "Documents",
                "docs": "Documents", "pdfs": "pdf", "videos": "Videos", "music": "Music", "audio": "Music",
                "archives": "Archives", "zips": "Archives", "installers": "Installers", "code": "Code"}
WHEN = {"today": 1, "yesterday": 2, "this week": 7, "week": 7, "this month": 31, "month": 31, "this year": 366,
        "year": 366}
KNOWN = ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")


def human_size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def resolve_place(raw: str, home: Path) -> Path:
    """'Downloads', 'desktop/notes', '~/x', 'D:\\Photos' → a full path. Known folders follow OneDrive if Windows
    moved them there."""
    text = (raw or "").strip().strip('"')
    if not text or text.lower() in ("home", "~", "my files", "everywhere", "anywhere", "all"):
        return home
    if text.startswith("~"):
        return Path(text).expanduser()
    path = Path(text)
    if path.is_absolute():
        return path
    first, *rest = path.parts
    for name in KNOWN:
        if first.lower() == name.lower():
            base = home / name
            if not base.exists() and (home / "OneDrive" / name).exists():
                base = home / "OneDrive" / name
            return base.joinpath(*rest)
    return home / path


def _paths(value) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


class Finder:
    """Walks the allowed places, skipping blocked, hidden and system folders, within a time and size budget."""

    def __init__(self, policy: PathPolicy, killswitch=None):
        self.policy = policy
        self.killswitch = killswitch
        self.scanned = 0
        self.cut_short = False

    def walk(self, start: Path):
        deadline = time.monotonic() + SCAN_SECONDS
        stack = [start]
        while stack:
            folder = stack.pop()
            try:
                entries = list(os.scandir(folder))
            except OSError:
                continue
            for e in entries:
                self.scanned += 1
                if self.scanned > SCAN_ENTRIES or time.monotonic() > deadline:
                    self.cut_short = True
                    return
                if self.killswitch is not None and self.scanned % 2000 == 0:
                    self.killswitch.check()
                try:
                    if e.is_symlink():
                        continue
                    p = Path(e.path)
                    if e.is_dir(follow_symlinks=False):
                        if e.name.lower() in SKIP_DIRS or e.name.startswith(".") or self.policy.is_blocked(p):
                            continue
                        yield p, None
                        stack.append(p)
                    elif e.is_file(follow_symlinks=False):
                        if e.name.startswith(".") or self.policy.is_blocked(p):
                            continue
                        yield p, e.stat(follow_symlinks=False)
                except OSError:
                    continue


def matches(path: Path, st, words: list[str], pattern: str, kind: str, newer_than: float, min_bytes: int) -> bool:
    name = path.name.lower()
    if pattern and not fnmatch.fnmatch(name, pattern):
        return False
    if words and not all(w in name for w in words):
        return False
    if kind:
        if kind in TYPES:
            if path.suffix.lower() not in TYPES[kind]:
                return False
        elif path.suffix.lower() != "." + kind:
            return False
    if newer_than and (st is None or st.st_mtime < newer_than):
        return False
    if min_bytes and (st is None or st.st_size < min_bytes):
        return False
    return True


@register
class Files(Agent):
    name = "files"
    description = (
        "Finds and handles files ANYWHERE on this laptop that Prometheus may use (the user folder plus drives or "
        "folders added in Settings). Actions: find (query = words in the name or a pattern like *.pdf, where = "
        "folder or 'anywhere', kind = images/documents/videos/music/archives/code/pdf, modified = today/this week/"
        "this month/this year, min_size_mb), search_text (query = words that are INSIDE the file, e.g. 'invoice 4521'; "
        "searches PDFs, Word, Excel, PowerPoint and text files in Documents, Desktop, Downloads and OneDrive), "
        "list (folder), info (path), copy (paths, to), move (paths, to), "
        "rename (path, new_name), recycle (paths; sends to the Recycle Bin), make_folder (path), create (path = new "
        "file name with its type, e.g. Documents/notes.md, plan.docx, roadmap.pdf, budget.xlsx (Markdown tables "
        "become sheets with totals and charts), revision.pptx (# title, ## one slide each, - bullets, Notes: speaker "
        "notes); content = the full "
        "text in Markdown (headings, lists, [links](https://…) stay clickable in PDFs), "
        "or $stepN.summary to save what an earlier step wrote; Markdown headings become Word headings). find returns "
        "'paths' that later steps can use as $stepN.paths. Every change can be undone on the History page.")
    tools = {"read_files", "move_files"}
    private_outputs = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        action = str(inputs.get("action") or "").strip().lower().replace(" ", "_")
        if action not in READ_ACTIONS | CHANGE_ACTIONS:
            return StepResult("failed", f"'{action}' is not a file action. I can: "
                                        + ", ".join(sorted(READ_ACTIONS | CHANGE_ACTIONS)) + ".")
        ctx.use(self.name, "read_files")
        if action in CHANGE_ACTIONS:
            ctx.use(self.name, "move_files")
        try:
            return getattr(self, "_" + action)(inputs, ctx)
        except PathBlocked as e:
            return StepResult("failed", str(e))

    # -- helpers ------------------------------------------------------------------
    def _place(self, raw: str, ctx: AgentContext) -> Path:
        path = resolve_place(raw, ctx.home)
        resolved = ctx.policy.check_read(path)
        return resolved

    def _root(self, path: Path, ctx: AgentContext) -> Path:
        root = ctx.policy.root_for(path)
        if root is None:
            raise PathBlocked(f"Not allowed: {path}")
        return root

    # -- reading ------------------------------------------------------------------
    def _find(self, inputs: dict, ctx: AgentContext) -> StepResult:
        query = str(inputs.get("query") or "").strip().lower()
        pattern = query if any(c in query for c in "*?") else ""
        words = [] if pattern else [w for w in re.split(r"[\s,]+", query) if w]
        raw_kind = str(inputs.get("kind") or "").strip().lower()
        kind = KIND_ALIASES.get(raw_kind, raw_kind.lstrip(".") if raw_kind else "")
        days = WHEN.get(str(inputs.get("modified") or "").strip().lower(), 0)
        if not days:
            try:
                days = int(str(inputs.get("modified_days") or 0))
            except ValueError:
                days = 0
        newer_than = time.time() - days * 86400 if days else 0.0
        try:
            min_bytes = int(float(inputs.get("min_size_mb") or 0) * 1024 * 1024)
        except (TypeError, ValueError):
            min_bytes = 0
        if not (words or pattern or kind or newer_than or min_bytes):
            return StepResult("failed", "Tell me what to look for: words in the name, a kind of file or a date.")
        where = str(inputs.get("where") or "anywhere")
        starts = ctx.policy.roots() if where.strip().lower() in ("", "anywhere", "everywhere", "all") \
            else [self._place(where, ctx)]
        finder = Finder(ctx.policy, ctx.killswitch)
        found = []
        for start in starts:
            for path, st in finder.walk(start):
                if st is None and not (words and not kind and not newer_than and not min_bytes):
                    continue                     # folders only match plain name searches
                if matches(path, st, words, pattern, kind, newer_than, min_bytes):
                    found.append((path, st))
                    if len(found) >= FIND_LIMIT * 4:
                        break
        found.sort(key=lambda f: -(f[1].st_mtime if f[1] else 0))
        shown = found[:FIND_LIMIT]
        for p, st in shown[:OUTPUTS_SHOWN]:
            ctx.store.add_output(ctx.task_id, str(p), "found", is_dir=st is None)
        lines = [f"- {p}" + (f" ({human_size(st.st_size)}, {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d})"
                             if st else " (folder)") for p, st in shown]
        summary = f"Found {len(found)}{'+' if len(found) >= FIND_LIMIT * 4 else ''} matching item(s)"
        summary += f", newest first. Showing {len(shown)}:" if shown else "."
        if finder.cut_short:
            summary += " (The search stopped early because there are a lot of files; narrow it with a folder.)"
        if lines:
            summary += "\n" + "\n".join(lines)
        return StepResult("done", summary, {"paths": [str(p) for p, _ in shown]})

    def _search_text(self, inputs: dict, ctx: AgentContext) -> StepResult:
        from prometheus import file_index

        query = str(inputs.get("query") or "").strip()
        if not query:
            return StepResult("failed", "Tell me what words are in the file.")
        index = file_index.CURRENT
        if index is None:
            return StepResult("failed", "Searching inside files isn't set up.")
        hits = [h for h in index.search(query) if not ctx.policy.is_blocked(Path(h["path"]))]
        status = ctx.store.kv_get("file_index_status") or "I haven't read your files yet"
        if not hits:
            return StepResult("done", f"No file I've read mentions '{query}'. ({status}; files are read at night, "
                                      "or press 'Read my files now' in Settings.)", {"paths": []})
        for h in hits[:OUTPUTS_SHOWN]:
            ctx.store.add_output(ctx.task_id, h["path"], "found")
        lines = [f"- {h['path']}\n  …{h['passage']}…" for h in hits]
        return StepResult("done", f"Files that mention '{query}':\n" + "\n".join(lines),
                          {"paths": [h["path"] for h in hits]})

    def _list(self, inputs: dict, ctx: AgentContext) -> StepResult:
        folder = self._place(str(inputs.get("folder") or inputs.get("path") or inputs.get("where") or ""), ctx)
        if not folder.is_dir():
            return StepResult("failed", f"{folder} is not a folder.")
        items = []
        try:
            for e in os.scandir(folder):
                p = Path(e.path)
                if e.name.startswith(".") or ctx.policy.is_blocked(p) or e.is_symlink():
                    continue
                try:
                    if e.is_file() and is_system_file(p):
                        continue
                    items.append((not e.is_dir(), e.name.lower(), p, None if e.is_dir() else e.stat()))
                except OSError:
                    continue
        except OSError as e:
            return StepResult("failed", f"Can't open {folder}: {e.strerror or e}")
        items.sort()
        shown = items[:LIST_LIMIT]
        lines = [f"- {p.name}/" if st is None else f"- {p.name} ({human_size(st.st_size)})" for _, _, p, st in shown]
        more = f" (showing {LIST_LIMIT})" if len(items) > LIST_LIMIT else ""
        return StepResult("done", f"{folder} has {len(items)} item(s){more}:\n" + "\n".join(lines),
                          {"paths": [str(p) for _, _, p, _ in shown], "folder": str(folder)})

    def _info(self, inputs: dict, ctx: AgentContext) -> StepResult:
        path = self._place(str(inputs.get("path") or ""), ctx)
        if not path.exists():
            return StepResult("failed", f"{path} doesn't exist.")
        ctx.store.add_output(ctx.task_id, str(path), "found", is_dir=path.is_dir())
        st = path.stat()
        when = f"changed {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}"
        if path.is_dir():
            finder = Finder(ctx.policy, ctx.killswitch)
            count = total = 0
            for _, fst in finder.walk(path):
                if fst is not None:
                    count += 1
                    total += fst.st_size
            extra = " (stopped counting early)" if finder.cut_short else ""
            text = f"{path}: folder with {count} files, {human_size(total)}{extra}, {when}."
        else:
            text = f"{path}: {human_size(st.st_size)}, {path.suffix or 'no extension'}, {when}."
        return StepResult("done", text, {"paths": [str(path)]})

    # -- changing -----------------------------------------------------------------
    def _run_changes(self, ctx: AgentContext, root: Path, work, kind: str = "created") -> tuple[list[str], list[str]]:
        files: FileActions = ctx.files
        run = files.journal.start(root)
        done, problems = [], []
        try:
            for label, call in work:
                ctx.killswitch.check()
                res = call(run)
                if res.status == "done" and res.new_path is not None:
                    ctx.store.add_output(ctx.task_id, str(res.new_path), kind, is_dir=Path(res.new_path).is_dir())
                (done if res.status == "done" else problems).append(
                    f"{label}" + (f" → {res.new_path}" if res.status == "done" and res.new_path else f": {res.detail}"
                                  if res.status != "done" else ""))
        finally:
            files.journal.finish(run)
        return done, problems

    def _report(self, verb: str, done: list[str], problems: list[str]) -> StepResult:
        text = f"{verb} {len(done)} item(s)."
        if done:
            text += "\n" + "\n".join(f"- {d}" for d in done[:50])
        if problems:
            text += "\nSkipped:\n" + "\n".join(f"- {p}" for p in problems[:50])
        if done:
            text += "\nYou can undo this on the History page."
        made = [d.split(" → ", 1)[1] for d in done if " → " in d]
        return StepResult("done" if done or not problems else "failed", text, {"made": made})

    def verify(self, goal: str, inputs: dict, result: StepResult, ctx: AgentContext) -> str:
        """After a change: are the new or moved files really there?"""
        made = result.data.get("made") or []
        missing = [m for m in made if not Path(m).exists()]
        return f"Said it was done, but these aren't there: {', '.join(Path(m).name for m in missing[:5])}." \
            if missing else ""

    def _copy(self, inputs: dict, ctx: AgentContext) -> StepResult:
        return self._copy_or_move(inputs, ctx, move=False)

    def _move(self, inputs: dict, ctx: AgentContext) -> StepResult:
        return self._copy_or_move(inputs, ctx, move=True)

    def _copy_or_move(self, inputs: dict, ctx: AgentContext, move: bool) -> StepResult:
        sources = _paths(inputs.get("paths") or inputs.get("path"))
        if not sources or not str(inputs.get("to") or "").strip():
            return StepResult("failed", "Say which files and the folder to put them in (inputs: paths, to).")
        dest = resolve_place(str(inputs["to"]), ctx.home)
        dest_root = self._root(ctx.policy.check_read(dest), ctx)
        work = []
        for raw in sources:
            src = resolve_place(raw, ctx.home)
            name = Path(raw).name
            if move:
                src_root = ctx.policy.root_for(src)
                if src_root is not None and src_root != dest_root:
                    # across drives or places: copy here, the original stays where it was
                    work.append((name + " (copied: moving between drives keeps the original)",
                                 lambda run, s=src: ctx.files.copy(run, s, dest, dest_root)))
                    continue
                work.append((name, lambda run, s=src: ctx.files.move(run, s, dest, dest_root)))
            else:
                work.append((name, lambda run, s=src: ctx.files.copy(run, s, dest, dest_root)))
        done, problems = self._run_changes(ctx, dest_root, work, "moved" if move else "created")
        return self._report("Moved" if move else "Copied", done, problems)

    def _rename(self, inputs: dict, ctx: AgentContext) -> StepResult:
        src = resolve_place(str(inputs.get("path") or ""), ctx.home)
        new_name = str(inputs.get("new_name") or "").strip()
        if Path(new_name).suffix.lower() in RUNNABLE or new_name.lower().endswith(tuple(RUNNABLE)):
            return StepResult("failed", f"Prometheus never makes {Path(new_name).suffix} files: Windows runs them when "
                                        "opened.")
        root = self._root(ctx.policy.check_read(src), ctx)
        done, problems = self._run_changes(ctx, root, [(src.name, lambda run: ctx.files.rename(run, src, new_name,
                                                                                                 root))], "renamed")
        return self._report("Renamed", done, problems)

    def _make_folder(self, inputs: dict, ctx: AgentContext) -> StepResult:
        folder = resolve_place(str(inputs.get("path") or inputs.get("folder") or ""), ctx.home)
        root = self._root(folder, ctx)
        done, problems = self._run_changes(ctx, root, [(str(folder), lambda run: ctx.files.make_folder(run, folder,
                                                                                                         root))])
        return self._report("Made", done, problems)

    def _create(self, inputs: dict, ctx: AgentContext) -> StepResult:
        raw = str(inputs.get("path") or inputs.get("name") or "").strip()
        content = inputs.get("content")
        content = "" if content is None else (content if isinstance(content, str) else str(content))
        if not raw:
            return StepResult("failed", "Say the new file's name, for example Documents/notes.md.")
        if len(Path(raw).parts) == 1:              # just a name: it goes in Documents
            raw = "Documents/" + raw
        path = resolve_place(raw, ctx.home)
        suffix = path.suffix.lower()
        if not suffix:
            path, suffix = path.with_suffix(".txt"), ".txt"
        if suffix in RUNNABLE:
            return StepResult("failed", f"Prometheus never creates {suffix} files: Windows runs them when opened.")
        if suffix not in TEXT_TYPES and suffix not in (".docx", ".pdf", ".xlsx", ".pptx"):
            return StepResult("failed", f"I can create text, Markdown, CSV, code, Word (.docx), PDF, Excel (.xlsx) "
                                        f"and PowerPoint (.pptx) files, not {suffix}.")
        if suffix in (".xlsx", ".pptx"):
            from prometheus.actions.office import markdown_pptx, markdown_xlsx

            data = (markdown_xlsx if suffix == ".xlsx" else markdown_pptx)(content, path.stem)
        elif suffix == ".pdf":
            from prometheus.actions.pdf import markdown_pdf

            data = markdown_pdf(content, path.stem)
        else:
            data = word_document(content) if suffix == ".docx" else content.encode("utf-8")
        if len(data) > MAX_NEW_FILE:
            return StepResult("failed", "That is too much text for one new file.")
        root = self._root(path, ctx)
        work = [(path.name, lambda run: ctx.files.create(run, path, data, root))]
        done, problems = self._run_changes(ctx, root, work)
        result = self._report("Created", done, problems)
        made = [d.split(" → ", 1)[1] for d in done if " → " in d]
        result.data["paths"] = made
        return result

    def _recycle(self, inputs: dict, ctx: AgentContext) -> StepResult:
        sources = [str(resolve_place(p, ctx.home)) for p in _paths(inputs.get("paths") or inputs.get("path"))]
        if not sources:
            return StepResult("failed", "Say which files to send to the Recycle Bin (input: paths).")
        for s in sources:                       # refuse early, before anything is queued
            ctx.policy.check_change(s, self._root(Path(s), ctx))
        if len(sources) > RECYCLE_WITHOUT_ASKING:
            names = ", ".join(Path(s).name for s in sources[:8]) + ("…" if len(sources) > 8 else "")
            ctx.approvals.request("recycle_files", f"Send {len(sources)} items to the Recycle Bin: {names}",
                                  {"paths": sources})
            return StepResult("waiting_approval", f"Sending {len(sources)} items to the Recycle Bin is waiting for "
                                                  "your approval on the Approvals page.")
        return recycle_paths(sources, ctx.policy, ctx.files, ctx.killswitch)


def word_document(text: str) -> bytes:
    """A .docx from plain text or simple Markdown: # headings, - bullets, 1. numbered items, **bold** stays as text."""
    import io

    import docx

    doc = docx.Document()
    for line in (text or "").splitlines():
        stripped = line.strip()
        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            doc.add_heading(heading.group(2).strip(), level=len(heading.group(1)))
        elif re.match(r"^[-*•]\s+", stripped):
            doc.add_paragraph(re.sub(r"^[-*•]\s+", "", stripped), style="List Bullet")
        elif re.match(r"^\d+[.)]\s+", stripped):
            doc.add_paragraph(re.sub(r"^\d+[.)]\s+", "", stripped), style="List Number")
        elif stripped:
            doc.add_paragraph(stripped.replace("**", ""))
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue()


def recycle_paths(paths: list[str], policy: PathPolicy, files: FileActions, killswitch=None) -> StepResult:
    by_root: dict[Path, list[str]] = {}
    for p in paths:
        root = policy.root_for(p)
        if root is not None:
            by_root.setdefault(root, []).append(p)
    done, problems = [], [f"{Path(p).name}: not allowed" for p in paths if policy.root_for(p) is None]
    for root, group in by_root.items():
        run = files.journal.start(root)
        try:
            for p in group:
                if killswitch is not None:
                    killswitch.check()
                res = files.recycle(run, Path(p), root)
                (done if res.status == "done" else problems).append(
                    Path(p).name if res.status == "done" else f"{Path(p).name}: {res.detail}")
        finally:
            files.journal.finish(run)
    text = f"Sent {len(done)} item(s) to the Recycle Bin."
    if done:
        text += " Restore them from the Recycle Bin if needed.\n" + "\n".join(f"- {d}" for d in done[:50])
    if problems:
        text += "\nSkipped:\n" + "\n".join(f"- {p}" for p in problems[:50])
    return StepResult("done" if done or not problems else "failed", text)


def recycle_handler(payload: dict, policy: PathPolicy, files: FileActions) -> str:
    """Runs after the owner approves a big Recycle Bin request. Paths are checked again at this moment."""
    return recycle_paths([str(p) for p in payload.get("paths", [])][:5000], policy, files).summary
