"""Files added to a message with the + button or by dropping them on the window (M2).

Kept in Prometheus's data folder (attachments/<day>/) for a few days, then removed. Only these kinds are taken:
images, PDFs, text and Markdown, Word, spreadsheets and CSV, code, ZIP archives and audio. A ZIP is never unpacked
or run: Prometheus only lists what's inside. Limits (size, count, days kept) are in Settings → Advanced.
"""
from __future__ import annotations

import re
import shutil
import time
import zipfile
from pathlib import Path

IMAGES = {".png", ".jpg", ".jpeg", ".webp"}
DOCUMENTS = {".pdf", ".txt", ".md", ".docx", ".doc", ".rtf", ".odt", ".pptx"}
SHEETS = {".xlsx", ".xls", ".csv", ".ods", ".tsv"}
CODE = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".hpp", ".cs", ".go", ".rs", ".rb", ".php",
        ".html", ".css", ".json", ".yaml", ".yml", ".toml", ".xml", ".sh", ".ps1", ".bat", ".sql", ".kt", ".swift",
        ".ipynb", ".lua", ".r", ".m", ".dart", ".vue", ".svelte", ".ini", ".cfg", ".log"}
ARCHIVES = {".zip"}
AUDIO = {".mp3", ".m4a", ".wav", ".ogg", ".opus", ".flac"}
ALLOWED = IMAGES | DOCUMENTS | SHEETS | CODE | ARCHIVES | AUDIO
ZIP_LIST_MAX = 60


class AttachmentError(ValueError):
    """Shown to the owner as is: plain words, no technical detail."""


def kind_of(name: str) -> str:
    s = Path(name).suffix.lower()
    for label, group in (("image", IMAGES), ("document", DOCUMENTS), ("spreadsheet", SHEETS), ("code", CODE),
                         ("zip", ARCHIVES), ("audio", AUDIO)):
        if s in group:
            return label
    return ""


def folder(data_dir: Path) -> Path:
    return Path(data_dir) / "attachments"


def save(data_dir: Path, name: str, data: bytes, max_mb: int) -> Path:
    suffix = Path(name).suffix.lower()
    if suffix not in ALLOWED:
        raise AttachmentError(f"I can't take {suffix or 'that kind of'} files. Images, PDFs, documents, spreadsheets, "
                              "code, ZIP and audio files work.")
    if len(data) > max_mb * 1024 * 1024:
        raise AttachmentError(f"{Path(name).name} is bigger than {max_mb} MB (the limit in Settings).")
    day = folder(data_dir) / time.strftime("%Y-%m-%d")
    day.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w .-]", "_", Path(name).stem)[:60].strip(" .") or "file"
    target = day / f"{time.strftime('%H%M%S')} {stem}{suffix}"
    n = 1
    while target.exists():
        n += 1
        target = day / f"{time.strftime('%H%M%S')} {stem} ({n}){suffix}"
    target.write_bytes(data)
    return target


def describe(path: Path) -> str:
    """One line for the request: the file, and for a ZIP the names inside (never unpacked)."""
    line = f"- {path} ({kind_of(path.name) or 'file'})"
    if path.suffix.lower() == ".zip":
        try:
            with zipfile.ZipFile(path) as z:
                names = [i.filename for i in z.infolist() if not i.is_dir()]
            shown = ", ".join(names[:ZIP_LIST_MAX]) + (f" and {len(names) - ZIP_LIST_MAX} more" if len(names) > ZIP_LIST_MAX else "")
            line += f": a ZIP holding {len(names)} files ({shown}). It was not unpacked."
        except (zipfile.BadZipFile, OSError):
            line += ": a ZIP file that couldn't be opened."
    return line


def request_text(text: str, paths: list[Path]) -> str:
    """What the message becomes. Files without words: Prometheus asks what to do with them."""
    if not paths:
        return text
    listing = "\n".join(describe(p) for p in paths)
    if text.strip():
        return f"{text.strip()}\n\nAttached files:\n{listing}"
    return (f"I attached these files:\n{listing}\nTell me briefly what each one is, then ask me what I'd like to do "
            "with them.")


def clean_old(data_dir: Path, keep_days: int) -> int:
    """Remove attachment days older than keep_days. Returns how many day folders went."""
    root = folder(data_dir)
    if not root.is_dir():
        return 0
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for day in root.iterdir():
        if day.is_dir() and not day.is_symlink() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day.name):
            try:
                if time.mktime(time.strptime(day.name, "%Y-%m-%d")) + 86400 < cutoff:
                    shutil.rmtree(day)
                    removed += 1
            except (ValueError, OSError):
                continue
    return removed
