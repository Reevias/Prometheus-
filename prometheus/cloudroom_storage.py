"""Cloudroom storage (M6 6.3): what Prometheus knows, kept encrypted in a "Prometheus Cloudroom" folder on Google Drive.

What goes there (only with the Features switch "Cloudroom storage" on, off at the start):
- repo knowledge (the notes of accepted repos, M4.7), sync data (the same export the cloud copy uses) and the private
  notes (title and messages) of chats you left out in Memory -> Privacy;
- the nightly backup, sealed by backup.py, instead of the old "Prometheus Backups" folder.

Rules, all in code:
- Everything is sealed on the laptop with backup.py's AES-256-GCM (the key made from your recovery code, kept on this
  laptop) BEFORE it is written to the outbox. The outbox holds only sealed files, and flush() refuses (and deletes) a
  file in it that doesn't start with the sealed header, so nothing can reach Drive unencrypted.
- The laptop copy is the main one. If the file on Drive isn't the one this laptop uploaded last (someone changed it),
  the laptop's copy is uploaded as the main file and the other one is kept in "Other copies".
- Works offline: sealing and queuing need no connection; uploads wait in the outbox (they survive a restart) and go the
  next time flush() has a connection. Nothing is lost and nothing crashes.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import shutil
import tempfile
import threading
import time
from pathlib import Path

import httpx

from prometheus import backup

FOLDER = "Prometheus Cloudroom"
OTHER = "Other copies"
KINDS = {"knowledge": "Repo knowledge", "backups": "Backups", "sync": "Sync data", "private": "Private notes"}
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()-]{0,120}$")
STATE_KEY = "cloudroom_state"                 # name -> {sha, id, at}: what this laptop last uploaded
KNOWLEDGE, SYNC = "repo-knowledge.pcr", "sync-data.pcr"
FILES = "https://www.googleapis.com/drive/v3/files"
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FORGET_KEY = "cloudroom_forget"               # names to remove from Drive (a chat deleted or no longer left out)
HEADER = b"PCR-ITEM "                         # inside the sealed part: "PCR-ITEM <kind>/<name>\n", checked on opening
# one lock for every Storage: "Backup now" and the nightly job must never upload the same outbox at once
_LOCK = threading.RLock()


class Offline(Exception):
    """There is no connection to Drive right now (uploads wait)."""


class StorageError(Exception):
    pass


def enabled(settings) -> bool:
    from prometheus.features import on

    return settings is not None and on(settings, "cloudroom_storage")


def outbox_dir() -> Path:
    from prometheus.config import data_dir

    return data_dir() / "cloudroom" / "outbox"


# -- Google Drive ---------------------------------------------------------------------------------------------------
class GoogleDrive:
    """The few Drive calls Cloudroom storage needs, on top of the existing Google connector."""

    def __init__(self, google=None):
        if google is None:
            from prometheus.connectors.google import Google

            google = Google()
        self.g = google

    def _c(self, method: str, url: str, **kw):
        from prometheus.connectors.google import GoogleError

        try:
            return self.g._call(method, url, **kw)
        except GoogleError as e:
            if isinstance(e.__cause__, httpx.TransportError):
                raise Offline(str(e)) from e
            raise

    @staticmethod
    def _q(text: str) -> str:
        return text.replace("\\", " ").replace("'", " ")

    def ensure_folder(self, name: str, parent: str | None = None) -> str:
        q = (f"name = '{self._q(name)}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
             + (f" and '{parent}' in parents" if parent else ""))
        found = self._c("GET", FILES, params={"q": q, "fields": "files(id)", "pageSize": 1}).json().get("files", [])
        if found:
            return found[0]["id"]
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder", **({"parents": [parent]} if parent else {})}
        return self._c("POST", FILES, json=body).json()["id"]

    def find(self, name: str, parent: str) -> dict | None:
        q = f"name = '{self._q(name)}' and '{parent}' in parents and trashed = false"
        files = self._c("GET", FILES, params={"q": q, "fields": "files(id,appProperties)", "pageSize": 1}).json().get("files", [])
        return {"id": files[0]["id"], "sha": (files[0].get("appProperties") or {}).get("sha", "")} if files else None

    def put(self, name: str, path: Path, parent: str, sha: str, replace_id: str | None = None) -> str:
        meta = {"appProperties": {"sha": sha}}
        if replace_id:
            start = self._c("PATCH", f"{UPLOAD}/{replace_id}", params={"uploadType": "resumable"}, json=meta)
        else:
            start = self._c("POST", UPLOAD, params={"uploadType": "resumable"},
                            json={"name": name, "parents": [parent], "mimeType": "application/octet-stream", **meta})
        url = start.headers.get("Location")
        if not url:
            raise StorageError("Google Drive didn't accept the upload.")
        total = Path(path).stat().st_size
        try:
            with open(path, "rb") as f:
                sent = 0
                while True:
                    piece = f.read(backup.CHUNK)
                    end = sent + len(piece) - 1
                    headers = {"Authorization": f"Bearer {self.g._token()}",
                               "Content-Range": f"bytes {sent}-{end}/{total}" if piece else f"bytes */{total}"}
                    resp = self.g.http.put(url, content=piece, headers=headers, timeout=300)
                    if resp.status_code in (200, 201):
                        return resp.json().get("id", replace_id or "")
                    if resp.status_code != 308 or not piece:
                        raise StorageError(f"Upload stopped (Google said {resp.status_code}).")
                    sent += len(piece)
        except httpx.TransportError as e:
            raise Offline(f"Couldn't reach Google ({type(e).__name__}).") from e

    def fetch(self, file_id: str, dst: Path) -> None:
        Path(dst).write_bytes(self._c("GET", f"{FILES}/{file_id}", params={"alt": "media"}).content)

    def keep_other_copy(self, file_id: str, from_parent: str, to_parent: str, new_name: str) -> None:
        self._c("PATCH", f"{FILES}/{file_id}", params={"addParents": to_parent, "removeParents": from_parent,
                                                      "fields": "id"}, json={"name": new_name})

    def list(self, parent: str) -> list[dict]:
        files = self._c("GET", FILES, params={"q": f"'{parent}' in parents and trashed = false", "pageSize": 100,
                                              "orderBy": "createdTime desc",
                                              "fields": "files(id,name,createdTime)"}).json().get("files", [])
        return [{"id": f["id"], "name": f["name"], "order": f.get("createdTime", "")} for f in files]

    def delete(self, file_id: str) -> None:
        self._c("DELETE", f"{FILES}/{file_id}")


# -- the storage ----------------------------------------------------------------------------------------------------
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _check(kind: str, name: str) -> None:
    if kind not in KINDS:
        raise StorageError(f"Unknown kind of item: {kind!r}.")
    if not NAME.match(name or "") or ".." in name:
        raise StorageError("That name can't be stored (letters, digits, spaces and . _ ( ) - only).")


class Storage:
    def __init__(self, store, outbox: Path, drive=None, key=None):
        self.store, self.outbox, self.drive = store, Path(outbox), drive
        self._key_source = key
        self._lock = _LOCK

    # -- the key (the one backups use) --------------------------------------------------------------------------
    def _key(self) -> tuple[bytes, bytes]:
        try:
            return (self._key_source or backup._stored_key)()
        except backup.BackupError as e:
            raise StorageError(str(e)) from e

    # -- the outbox: sealed files waiting to go, each with a small .json note ------------------------------------
    def _paths(self, name: str) -> tuple[Path, Path]:
        base = self.outbox / hashlib.sha256(name.encode()).hexdigest()[:20]
        return base.with_suffix(".blob"), base.with_suffix(".blob.json")

    def _metas(self) -> list[tuple[Path, dict]]:
        out = []
        if not self.outbox.exists():
            return out
        for note in sorted(self.outbox.glob("*.blob.json")):
            try:
                meta = json.loads(note.read_text(encoding="utf-8"))
                out.append((note.with_suffix(""), meta))
            except (OSError, ValueError):
                note.unlink(missing_ok=True)
        return out

    def pending(self) -> list[str]:
        return sorted(m["name"] for _, m in self._metas())

    def _tag(self, kind: str, name: str, sha: str) -> str:
        """What Drive is told about a file: made with the key, so it gives away nothing about the content and can't
        be faked (it was the plain SHA-256 before the review of 6.3)."""
        _salt, key = self._key()
        return hmac.new(key, f"{kind}/{name}/{sha}".encode("utf-8"), hashlib.sha256).hexdigest()

    def _state(self) -> dict:
        s = self.store.kv_get(STATE_KEY, {})
        return s if isinstance(s, dict) else {}

    def _unchanged(self, name: str, sha: str) -> bool:
        waiting = next((m for _, m in self._metas() if m["name"] == name), None)
        if waiting is not None:
            return waiting["sha"] == sha
        return self._state().get(name, {}).get("sha") == sha

    def _add(self, kind: str, name: str, sha: str, write) -> None:
        blob, note = self._paths(name)
        self.outbox.mkdir(parents=True, exist_ok=True)
        tmp = blob.with_suffix(".tmp")
        write(tmp)
        os.replace(tmp, blob)
        note_tmp = note.with_suffix(".tmp")
        note_tmp.write_text(json.dumps({"kind": kind, "name": name, "sha": sha, "queued": time.time()}), encoding="utf-8")
        os.replace(note_tmp, note)

    def queue(self, kind: str, name: str, plain: bytes) -> bool:
        """Seal `plain` on this laptop and put it in the outbox. False when it is the same as what is already there."""
        _check(kind, name)
        with self._lock:
            sha = _sha(plain)
            if self._unchanged(name, sha):
                return False
            salt, key = self._key()
            # the kind and name go inside the sealed part, so a file moved onto another name is refused when opened
            sealed = backup.encrypt_bytes(HEADER + f"{kind}/{name}\n".encode("utf-8") + plain, salt, key)
            self._add(kind, name, sha, lambda p: Path(p).write_bytes(sealed))
            return True

    def queue_sealed(self, kind: str, name: str, sealed) -> bool:
        """Queue something already sealed by backup.py (the nightly backup): bytes or a file."""
        _check(kind, name)
        with self._lock:
            if isinstance(sealed, (bytes, bytearray)):
                head, sha = bytes(sealed[:len(backup.MAGIC2)]), _sha(bytes(sealed))
                write = lambda p: Path(p).write_bytes(sealed)          # noqa: E731
            else:
                src = Path(sealed)
                with open(src, "rb") as f:
                    head = f.read(len(backup.MAGIC2))
                h = hashlib.sha256()
                with open(src, "rb") as f:
                    for piece in iter(lambda: f.read(1 << 20), b""):
                        h.update(piece)
                sha, write = h.hexdigest(), (lambda p: shutil.copyfile(src, p))
            if head != backup.MAGIC2:
                raise StorageError("That file isn't sealed, so it will not be uploaded.")
            if self._unchanged(name, sha):
                return False
            self._add(kind, name, sha, write)
            if kind == "backups":           # offline for weeks: keep only the newest backups waiting (disk space)
                waiting = sorted(((m.get("queued", 0), m["name"], b) for b, m in self._metas()
                                  if m.get("kind") == "backups"), reverse=True)
                for _q, _n, old in waiting[backup.KEEP:]:
                    old.unlink(missing_ok=True)
                    old.with_suffix(".blob.json").unlink(missing_ok=True)
            return True

    # -- sending ------------------------------------------------------------------------------------------------------
    def _drive(self):
        if self.drive is None:
            self.drive = GoogleDrive()
        return self.drive

    def flush(self) -> dict:
        from prometheus.connectors.google import GoogleError

        res = {"sent": 0, "waiting": 0, "clashes": 0, "refused": 0, "message": ""}
        with self._lock:
            if self.outbox.exists():
                for half in self.outbox.glob("*.tmp"):            # left by a crash halfway through writing
                    half.unlink(missing_ok=True)
                known = {b for b, _ in self._metas()}
                for stray in self.outbox.glob("*.blob"):
                    if stray not in known:
                        stray.unlink(missing_ok=True)
            metas = self._metas()
            ready = []
            for blob, meta in metas:                 # nothing unsealed ever goes up
                try:
                    with open(blob, "rb") as f:
                        sealed = f.read(len(backup.MAGIC2)) == backup.MAGIC2
                except OSError:
                    sealed = False
                if sealed:
                    ready.append((blob, meta))
                else:
                    res["refused"] += 1
                    blob.unlink(missing_ok=True)
                    blob.with_suffix(".blob.json").unlink(missing_ok=True)
            memo: dict = {}
            try:
                for blob, meta in ready:
                    self._send(blob, meta, res, memo)
                self._forget(memo)
            except Offline:
                res["message"] = "Offline: the uploads wait in the outbox and go when there is a connection."
            except GoogleError as e:
                res["message"] = str(e)
            res["waiting"] = len(self.pending())
        return res

    def _folder(self, memo: dict, kind: str | None) -> str:
        d = self._drive()
        if "root" not in memo:
            memo["root"] = d.ensure_folder(FOLDER)
        if kind is None:
            return memo["root"]
        if kind not in memo:
            memo[kind] = d.ensure_folder(OTHER if kind == "other" else KINDS[kind], memo["root"])
        return memo[kind]

    def _forget(self, memo: dict) -> None:
        """Remove from Drive what the laptop no longer keeps (the laptop copy is the main one)."""
        names = self.store.kv_get(FORGET_KEY, []) or []
        if not names:
            return
        state = self._state()
        for name in list(names):
            there = self._drive().find(name, self._folder(memo, "private"))
            if there:
                self._drive().delete(there["id"])
            state.pop(name, None)
            names.remove(name)
            self.store.kv_set(STATE_KEY, state)
            self.store.kv_set(FORGET_KEY, names)

    def _send(self, blob: Path, meta: dict, res: dict, memo: dict) -> None:
        d, name, kind, sha = self._drive(), meta["name"], meta["kind"], meta["sha"]
        tag = self._tag(kind, name, sha)
        parent = self._folder(memo, kind)
        there = d.find(name, parent)
        last = self._state().get(name, {})
        if there and there["sha"] == tag:
            fid = there["id"]                                              # already there
        elif there and last.get("tag") and last.get("tag") == there["sha"]:
            fid = d.put(name, blob, parent, tag, replace_id=there["id"])   # our own older copy: replace it
            res["sent"] += 1
        else:
            if there:                                                      # a clash: the laptop wins
                d.keep_other_copy(there["id"], parent, self._folder(memo, "other"),
                                  f"{name} (other copy {time.strftime('%Y-%m-%d %H%M%S')})")
                res["clashes"] += 1
            fid = d.put(name, blob, parent, tag)
            res["sent"] += 1
        state = self._state()
        state[name] = {"sha": sha, "tag": tag, "id": fid, "at": time.time()}
        self.store.kv_set(STATE_KEY, state)
        blob.unlink(missing_ok=True)
        blob.with_suffix(".blob.json").unlink(missing_ok=True)
        if kind == "backups":
            self._prune(parent)

    def _prune(self, parent: str) -> None:
        files = [f for f in self._drive().list(parent) if f["name"].endswith(".pbak")]
        files.sort(key=lambda f: f["order"], reverse=True)
        for old in files[backup.KEEP:]:
            self._drive().delete(old["id"])

    # -- reading it back ------------------------------------------------------------------------------------------------
    def open_item(self, kind: str, name: str) -> bytes:
        """The plain content of a stored item (from the outbox if it hasn't gone up yet, else from Drive)."""
        _check(kind, name)
        salt, key = self._key()
        with self._lock:
            for blob, meta in self._metas():
                if meta["name"] == name:
                    return self._unwrap(kind, name, backup.decrypt_bytes(blob.read_bytes(), key, salt))
        d = self._drive()
        root = d.ensure_folder(FOLDER)
        there = d.find(name, d.ensure_folder(KINDS[kind], root))
        if not there:
            raise StorageError("Nothing is stored under that name.")
        with tempfile.TemporaryDirectory(prefix="prom-cloudroom-") as tmp:
            got = Path(tmp) / "item"
            d.fetch(there["id"], got)
            return self._unwrap(kind, name, backup.decrypt_bytes(got.read_bytes(), key, salt))

    @staticmethod
    def _unwrap(kind: str, name: str, plain: bytes) -> bytes:
        if kind == "backups":                       # sealed whole by backup.py: opened by restore, not here
            return plain
        want = HEADER + f"{kind}/{name}\n".encode("utf-8")
        if not plain.startswith(want):
            raise StorageError("That stored file doesn't belong to this name (it was moved or changed), so it wasn't "
                               "used.")
        return plain[len(want):]

    def lookup_private(self, chat_id: int) -> dict:
        """The stored private notes of a chat that was left out: {chat_id, title, messages}."""
        return json.loads(self.open_item("private", f"private-chat-{int(chat_id)}.pcr"))

    # -- what to store ----------------------------------------------------------------------------------------------------
    def snapshot(self, services) -> list[str]:
        """Seal and queue what changed since the last time: repo knowledge, sync data, left-out chats' private notes."""
        from prometheus.sync import export

        store, queued = self.store, []

        def put(kind: str, name: str, obj) -> None:
            if self.queue(kind, name, json.dumps(obj, sort_keys=True, default=str).encode("utf-8")):
                queued.append(name)

        notes = []
        for row in store._rows("SELECT id, key, title, source, private FROM documents WHERE key LIKE 'repo:%' ORDER BY key"):
            text = "\n\n".join(r["text"] for r in store._rows(
                "SELECT text FROM chunks WHERE doc_id = ? AND parent_id IS NULL ORDER BY seq", (row["id"],)))
            notes.append({"key": row["key"], "title": row["title"], "source": row["source"],
                          "private": bool(row["private"]), "text": text})
        if notes:
            put("knowledge", KNOWLEDGE, {"version": 1, "notes": notes})
        put("sync", SYNC, export(store, 0))
        archive = getattr(services, "archive", None)
        left_out = getattr(archive, "left_out", None)
        if left_out is not None:
            keep = set()
            for cid, title in store.chats():
                if left_out(cid):
                    keep.add(f"private-chat-{cid}.pcr")
                    put("private", f"private-chat-{cid}.pcr", {
                        "chat_id": cid, "title": title,
                        "messages": [{"role": m.role, "text": m.text, "created": m.created} for m in store.messages(cid)]})
            # a chat deleted, or taken off "left out": its private notes go from the outbox and from Drive too
            with self._lock:
                gone = set()
                for blob, meta in self._metas():
                    if meta.get("kind") == "private" and meta["name"] not in keep:
                        blob.unlink(missing_ok=True)
                        blob.with_suffix(".blob.json").unlink(missing_ok=True)
                        gone.add(meta["name"])
                gone |= {n for n in self._state() if n.startswith("private-chat-") and n not in keep}
                if gone:
                    forget = set(self.store.kv_get(FORGET_KEY, []) or []) | gone
                    self.store.kv_set(FORGET_KEY, sorted(forget))
        return queued


def run_once(services, drive=None, outbox: Path | None = None) -> str:
    """The nightly job: seal what changed, then upload what is waiting. Does nothing with the switch off."""
    if not enabled(getattr(services, "settings", None)):
        return "Cloudroom storage is off."
    st = Storage(services.store, outbox or outbox_dir(), drive=drive)
    queued = st.snapshot(services)
    res = st.flush()
    return (f"Cloudroom storage: sealed {len(queued)} item(s), uploaded {res['sent']}, waiting {res['waiting']}."
            + (f" {res['message']}" if res["message"] else ""))


def queue_backup(services, sealed: Path, name: str) -> Storage:
    """Called by backup.run with the sealed backup while the switch is on; goes up with the next flush."""
    st = Storage(services.store, outbox_dir())
    st.queue_sealed("backups", name, Path(sealed))
    return st
