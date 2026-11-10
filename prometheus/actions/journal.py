"""Crash-safe undo journal: one JSON file per run, saved after every change."""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

RUN_ID = re.compile(r"^[0-9A-Za-z_-]+$")


class Journal:
    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active: set[str] = set()  # runs still being written by an agent

    def _file(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            raise ValueError("Invalid run id")
        return self.folder / f"{run_id}.json"

    def _save(self, run_id: str, data: dict) -> None:
        target = self._file(run_id)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, target)

    def start(self, root: Path) -> str:
        run_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
        self.active.add(run_id)
        self._save(run_id, {"id": run_id, "root": str(Path(root).resolve()), "created": time.time(),
                            "finished": None, "entries": [], "created_dirs": [], "undone": None})
        return run_id

    def load(self, run_id: str) -> dict:
        return json.loads(self._file(run_id).read_text(encoding="utf-8"))

    def update(self, run_id: str, data: dict) -> None:
        with self.lock:
            self._save(run_id, data)

    def record(self, run_id: str, entry: dict) -> None:
        with self.lock:
            data = self.load(run_id)
            data["entries"].append(entry)
            self._save(run_id, data)

    def record_dirs(self, run_id: str, dirs: list[str]) -> None:
        if not dirs:
            return
        with self.lock:
            data = self.load(run_id)
            data["created_dirs"].extend(dirs)
            self._save(run_id, data)

    def finish(self, run_id: str) -> None:
        with self.lock:
            data = self.load(run_id)
            data["finished"] = time.time()
            self._save(run_id, data)
            self.active.discard(run_id)

    def runs(self) -> list[dict]:
        out = []
        for f in self.folder.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                out.append({"id": data["id"], "root": data["root"], "created": data["created"],
                            "finished": data["finished"], "count": len(data["entries"]),
                            "undone": data.get("undone"), "last_undo": data.get("last_undo"),
                            "active": data["id"] in self.active})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(out, key=lambda r: r["created"], reverse=True)
