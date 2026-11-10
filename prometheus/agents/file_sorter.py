"""Sorts one folder: rules first, the local model only for unclear documents."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from prometheus.actions.files import stamp
from prometheus.agents.base import UNTRUSTED_RULE, Agent, AgentContext, StepResult, register, resolve_folder, untrusted
from prometheus.agents.textutil import extract_text
from prometheus.models.base import ProviderUnavailable
from prometheus.safety.paths import PathBlocked

TYPES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp", ".tif", ".tiff"},
    "Videos": {".mp4", ".mov", ".mkv", ".avi", ".webm"},
    "Music": {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"},
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".xls", ".xlsx", ".csv", ".ppt",
                  ".pptx"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz"},
    "Installers": {".exe", ".msi", ".dmg", ".apk", ".deb"},
    "Code": {".py", ".js", ".ts", ".html", ".css", ".java", ".c", ".cpp", ".go", ".rs", ".sh", ".ps1"},
}
UNCLEAR = re.compile(r"^[0-9a-f]{6,}|^(doc|scan|img)[-_]?\d+", re.IGNORECASE)
FOLDER_SCHEMA = {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"],
                 "additionalProperties": False}
SYSTEM_NAMES = {"desktop.ini", "thumbs.db", ".ds_store", "ehthumbs.db"}
HIDDEN_OR_SYSTEM = 0x2 | 0x4  # Windows FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM
SAFE_PART = re.compile(r"^[\w][\w .&()-]{0,60}$")


def is_system_file(path: Path) -> bool:
    if path.name.startswith(".") or path.name.lower() in SYSTEM_NAMES:
        return True
    return bool(getattr(path.stat(), "st_file_attributes", 0) & HIDDEN_OR_SYSTEM)


def category(path: Path) -> str:
    suffix = path.suffix.lower()
    for name, suffixes in TYPES.items():
        if suffix in suffixes:
            return name
    return "Other"


def photo_date(path: Path) -> datetime:
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            raw = exif.get_ifd(0x8769).get(36867) or exif.get(306)
        if raw:
            return datetime.strptime(str(raw).strip("\x00 "), "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def safe_documents_folder(answer: str) -> str:
    parts = [p for p in answer.replace("\\", "/").split("/") if p]
    if parts and parts[0] == "Documents":
        parts = parts[1:]
    if not parts or len(parts) > 1 or not all(SAFE_PART.match(p) and p not in (".", "..") for p in parts):
        return "Documents"
    return "Documents/" + parts[0]


@register
class FileSorter(Agent):
    name = "file_sorter"
    description = ("Sorts the files in one folder into subfolders (by type, photo date, duplicates). "
                   "Inputs: folder (e.g. 'Downloads'), layout ('auto', 'type' or 'date').")
    private_outputs = True
    tools = {"read_files", "move_files", "call_model"}

    def _ask_model(self, ctx: AgentContext, path: Path) -> str:
        ctx.use(self.name, "call_model")
        snippet = extract_text(path, limit=200)
        prompt = (f"Pick a short subfolder name inside Documents for this file.\nFile name: {path.name}\n"
                  + untrusted(snippet, path.name)
                  + '\nAnswer as JSON like {"folder": "Documents/Tax"}.')
        try:
            reply = ctx.router.complete("private", [{"role": "user", "content": prompt}],
                                        "You organise a person's files. " + UNTRUSTED_RULE,
                                        json_schema=FOLDER_SCHEMA, max_tokens=100)
            return safe_documents_folder(json.loads(reply.text).get("folder", ""))
        except (ProviderUnavailable, ValueError, AttributeError):
            return "Documents"

    def _destination(self, ctx: AgentContext, path: Path, layout: str) -> str:
        if layout == "date":
            d = datetime.fromtimestamp(path.stat().st_mtime)
            return f"{d:%Y}/{d:%m}"
        cat = category(path)
        if cat == "Images" and layout == "auto":
            d = photo_date(path)
            return f"Images/{d:%Y}/{d:%m}"
        if cat == "Documents" and UNCLEAR.match(path.stem):
            return self._ask_model(ctx, path)
        return cat

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        ctx.use(self.name, "move_files")
        layout = inputs.get("layout", "auto")
        try:
            root = ctx.policy.check_change(resolve_folder(str(inputs.get("folder", "Downloads")), ctx.home),
                                           resolve_folder(str(inputs.get("folder", "Downloads")), ctx.home))
        except PathBlocked as e:
            return StepResult("failed", f"Can't sort that folder: {e}")
        if not root.is_dir():
            return StepResult("failed", f"Folder not found: {root}")

        files = sorted(p for p in root.iterdir() if p.is_file() and not p.is_symlink() and not is_system_file(p))
        stamps = {p: stamp(p) for p in files}
        groups: dict[str, list[Path]] = {}
        for p in files:
            ctx.killswitch.check()
            groups.setdefault(sha256(p), []).append(p)
        duplicates = set()
        for same in groups.values():
            if len(same) > 1:
                keep = max(same, key=lambda p: stamps[p].mtime)
                duplicates.update(p for p in same if p != keep)

        run_id = ctx.files.journal.start(root)
        moved = skipped = 0
        pdfs: list[str] = []
        problems: list[str] = []
        for p in files:
            ctx.killswitch.check()
            dest = "Duplicates" if p in duplicates else self._destination(ctx, p, layout)
            result = ctx.files.move(run_id, p, root / dest, root, expected=stamps[p])
            final = result.new_path if result.status == "done" else p
            if result.status == "done":
                moved += 1
                ctx.store.audit(self.name, "move", {"from": str(p), "to": str(final), "run_id": run_id})
            else:
                skipped += 1
                problems.append(f"{p.name}: {result.detail}")
            if p.suffix.lower() == ".pdf" and p not in duplicates:
                pdfs.append(str(Path(final).resolve()))
        ctx.files.journal.finish(run_id)

        summary = f"Sorted {root.name}: moved {moved} files, skipped {skipped}, found {len(pdfs)} PDFs."
        if problems:
            summary += " Skipped: " + "; ".join(problems[:5])
        ctx.store.add_agent_note(ctx.task_id, self.name, summary)
        return StepResult("done", summary, {"run_id": run_id, "moved": moved, "skipped": skipped, "pdfs": pdfs})
