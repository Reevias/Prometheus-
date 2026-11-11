"""Photos: what is in them, the text in them, and sorting them into folders.

At night (with the other file reading) each new photo in Pictures, OneDrive Pictures, Desktop and Downloads is
looked at on the laptop: a small image model (CLIP, about 600 MB, downloaded once) picks what it shows (beach,
people, food, documents…) and OCR reads any text in it, so "the photo of my Aadhaar card" or "beach photos" can be
found. Nothing leaves the laptop. Sorting into folders (Pictures/Beach, Pictures/Documents…) happens only when the
owner asks, after a yes in the chat, and can be undone on the History page.

Needs:  pip install -e ".[photos]"
"""
from __future__ import annotations

import re
import threading
from pathlib import Path

from prometheus.file_index import FileIndex

PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
PHOTO_PLACES = ("Pictures", "OneDrive/Pictures", "Desktop", "Downloads")
# what the image model chooses from: (tag, folder, how it's described to the model)
KINDS = [
    ("beach", "Beach", "a photo of a beach or the sea"),
    ("mountains", "Mountains", "a photo of mountains or hills"),
    ("nature", "Nature", "a photo of trees, a garden, flowers or a park"),
    ("city", "City", "a photo of a city street or buildings"),
    ("people", "People", "a photo of a person or a selfie"),
    ("group", "People", "a group photo of several people"),
    ("food", "Food", "a photo of food or a meal"),
    ("pets", "Pets", "a photo of a dog, a cat or another pet"),
    ("document", "Documents", "a photo of a printed document or a page of text"),
    ("id card", "Documents", "a photo of an identity card or a certificate"),
    ("receipt", "Receipts", "a photo of a receipt or a bill"),
    ("notes", "Notes", "a photo of handwritten notes, a notebook or a whiteboard"),
    ("screenshot", "Screenshots", "a screenshot of a phone or computer screen"),
    ("event", "Events", "a photo of a party, a wedding or a celebration"),
    ("vehicle", "Vehicles", "a photo of a car, a bike or a vehicle"),
    ("meme", "Memes", "a meme or a cartoon"),
]
FOLDER = {tag: folder for tag, folder, _ in KINDS}
SORTED_FOLDERS = set(FOLDER.values())
SCREENSHOT_NAME = re.compile(r"screen\s?shot|screenshot|screen_?capture|snip", re.I)
STUCK = re.compile(r"(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])")
MIN_TEXT = 12
SORT_LIMIT = 2000

CURRENT = None          # the app's PhotoIndex


class Looker:
    """Loads the image model and OCR the first time they're needed (they use about 1 GB while working)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._clip = self._ocr = None
        self._labels = None

    def _load(self):
        if self._clip is None:
            import numpy as np
            from fastembed import ImageEmbedding, TextEmbedding

            from prometheus.config import data_dir

            cache = str(data_dir() / "models" / "photos")     # kept, so the download happens once
            self._clip = ImageEmbedding("Qdrant/clip-ViT-B-32-vision", cache_dir=cache)
            text = TextEmbedding("Qdrant/clip-ViT-B-32-text", cache_dir=cache)
            labels = np.array(list(text.embed([d for _, _, d in KINDS])))
            self._labels = labels / np.linalg.norm(labels, axis=1, keepdims=True)
        if self._ocr is None:
            from rapidocr_onnxruntime import RapidOCR

            self._ocr = RapidOCR()

    def tags(self, path: Path) -> list[str]:
        import numpy as np

        with self._lock:
            self._load()
            vec = np.array(list(self._clip.embed([str(path)]))[0])
        vec = vec / np.linalg.norm(vec)
        logits = 100 * (self._labels @ vec)
        probs = np.exp(logits - logits.max())
        probs /= probs.sum()
        order = np.argsort(-probs)
        out = [KINDS[order[0]][0]]
        out += [KINDS[i][0] for i in order[1:3] if probs[i] > 0.25]
        return out

    def text(self, path: Path) -> str:
        with self._lock:
            self._load()
            result, _ = self._ocr(str(path))
        words = " ".join(r[1] for r in (result or []) if r and len(r) > 1)
        return STUCK.sub(" ", words)


def is_screenshot(path: Path) -> bool:
    return bool(SCREENSHOT_NAME.search(path.name)) or "screenshots" in (p.lower() for p in path.parts)


class PhotoIndex(FileIndex):
    TABLE, SUFFIXES, MAX_BYTES, KV = "photo_index", PHOTO_SUFFIXES, 30 * 1024 * 1024, "photo_index_status"
    FEATURE = "photos"
    MAIN = PHOTO_PLACES

    def __init__(self, store, policy, extra_roots=(), looker=None, **kw):
        super().__init__(store, policy, (), **kw)
        self.looker = looker or Looker()
        with store._lock:
            store._db.execute("CREATE TABLE IF NOT EXISTS photo_tags (path TEXT PRIMARY KEY, tags TEXT NOT NULL)")
            store._db.commit()

    def places(self) -> list[Path]:
        home = self.policy.home
        out = [home / p for p in self.MAIN if (home / p).is_dir()]
        out += [p / "Pictures" for p in home.glob("OneDrive - *") if (p / "Pictures").is_dir()]
        from prometheus.phone import UPLOADS

        if (home / UPLOADS / "Photos").is_dir():
            out.append(home / UPLOADS / "Photos")          # photos sent from the phone
        return out

    def read(self, path: Path) -> str:
        tags = ["screenshot"] if is_screenshot(path) else []
        try:
            tags = list(dict.fromkeys(tags + self.looker.tags(path)))
        except ImportError:
            raise
        except Exception:
            pass
        try:
            words = self.looker.text(path)
        except ImportError:
            raise
        except Exception:
            words = ""
        if len(words) >= 40 and tags and tags[0] not in ("document", "id card", "receipt", "notes", "screenshot"):
            tags.insert(0, "document")                   # lots of text: it's a document, whatever it looks like
        with self.store._lock:
            self.store._db.execute("INSERT OR REPLACE INTO photo_tags (path, tags) VALUES (?, ?)",
                                   (str(path), ",".join(tags)))
            self.store._db.commit()
        text = f"Photo of: {', '.join(tags) or 'unknown'}"
        if len(words) >= MIN_TEXT:
            text += f"\nText in the photo: {words}"
        return text

    def run(self, budget_seconds: float = 3600, only_at_night: bool = False) -> dict:
        try:
            return super().run(budget_seconds, only_at_night)
        except ImportError:
            self.status = 'Photo reading needs one extra part. Run:  pip install -e ".[photos]"'
            self.store.kv_set(self.KV, self.status)
            return {"read": 0, "missing": True}

    def forget(self, key: str) -> None:
        super().forget(key)
        self.store._write("DELETE FROM photo_tags WHERE path = ?", (key,))

    # -- asking about photos ---------------------------------------------------------------
    def tagged(self) -> dict[str, list[str]]:
        rows = self.store._rows("SELECT path, tags FROM photo_tags")
        return {r["path"]: [t for t in r["tags"].split(",") if t] for r in rows}

    def find(self, query: str, limit: int = 30) -> list[str]:
        """Photos by what they show ('beach', 'food') and, failing that, by the text in them."""
        q = query.lower()
        wanted = {tag for tag, folder, _ in KINDS if re.search(rf"\b({re.escape(tag)}s?|{folder.lower()})\b", q)}
        out = [p for p, tags in self.tagged().items() if wanted & set(tags) and Path(p).exists()]
        if not out:
            out = [h["path"] for h in self.search(query, limit) if Path(h["path"]).suffix.lower() in PHOTO_SUFFIXES]
        return out[:limit]

    def sort_plan(self) -> dict[str, list[str]]:
        """Folder name → photos to move there (photos already in a sorted folder stay put)."""
        plan: dict[str, list[str]] = {}
        for path, tags in self.tagged().items():
            p = Path(path)
            if not tags or not p.exists() or p.parent.name in SORTED_FOLDERS:
                continue
            plan.setdefault(FOLDER.get(tags[0], "Other"), []).append(path)
        return plan


def sort_handler(payload: dict, policy, files) -> str:
    """After a yes in the chat: move each photo into Pictures/<Folder>. Undo on the History page."""
    home = policy.home
    run = files.journal.start(home)
    moved, skipped = 0, 0
    try:
        for folder, paths in payload.get("plan", {}).items():
            if folder not in SORTED_FOLDERS | {"Other"}:
                continue
            dest = home / "Pictures" / folder
            for raw in paths[:SORT_LIMIT]:
                src = Path(raw)
                if policy.root_for(src) != home or not src.exists():
                    skipped += 1
                    continue
                try:
                    if not dest.exists():
                        files.make_folder(run, dest, home)
                    res = files.move(run, src, dest, home)
                except Exception:
                    skipped += 1
                    continue
                if res.status == "done":
                    moved += 1
                    if CURRENT is not None:
                        CURRENT.store._write("UPDATE photo_tags SET path = ? WHERE path = ?", (str(res.new_path), raw))
                else:
                    skipped += 1
    finally:
        files.journal.finish(run)
    return (f"Moved {moved} photos into folders in {home / 'Pictures'}." + (f" {skipped} were skipped." if skipped
            else "") + " You can undo this on the History page.")
