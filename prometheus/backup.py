"""Encrypted backups of everything Prometheus knows, to the owner's Google Drive every night.

What's inside: memory.db (chats, facts, flashcards, spending, deadlines, reminders…), settings, keys and app
passwords, prompts and what it learned, skills and its own tools, the voice training, the chat archive folder, and
the Prometheus folders in Documents (study, money, lectures). Big model files and the code quarantine are left out.

It is encrypted on the laptop before it leaves (AES-256-GCM, in 8 MB pieces), with a key made from a recovery code
shown to the owner once. Google only ever stores scrambled bytes. To restore on a new laptop:
    prometheus restore <the .pbak file downloaded from Drive>   (it asks for the recovery code)
The newest 14 backups are kept in the Drive folder "Prometheus Backups".
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import secrets
import sqlite3
import struct
import tempfile
import time
import zipfile
from pathlib import Path

MAGIC = b"PROMBAK1"
CHUNK = 8 * 1024 * 1024
KEEP = 14
FOLDER = "Prometheus Backups"
KEY_NAME = "BACKUP_KEY"                    # the key made from the recovery code, kept on this laptop
SKIP_DIRS = {"models", "quarantine", "scripts", "screen", "browser", "__pycache__", "voice-models", "whisper"}
DOC_FOLDERS = ("Prometheus Study", "Prometheus Money", "Prometheus Lectures")
MAX_FILE = 200 * 1024 * 1024
UPLOAD = "https://www.googleapis.com/upload/drive/v3/files"
FILES = "https://www.googleapis.com/drive/v3/files"


class BackupError(Exception):
    pass


# -- the recovery code and the key -----------------------------------------------------------
def new_recovery_code() -> str:
    """Six groups of five letters and digits, easy to write down: e.g. 7K2QD-MX9FA-…"""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(30))
    return "-".join(raw[i:i + 5] for i in range(0, 30, 5))


def _norm(code: str) -> str:
    return "".join(c for c in (code or "").upper() if c.isalnum())


def derive_key(code: str, salt: bytes) -> bytes:
    return hashlib.scrypt(_norm(code).encode(), salt=salt, n=2 ** 15, r=8, p=1, maxmem=64 * 1024 * 1024, dklen=32)


def set_up(code: str | None = None) -> str:
    """Make (or take) the recovery code and keep its key on this laptop. Returns the code to show once."""
    from prometheus.config import set_secret

    code = code or new_recovery_code()
    salt = secrets.token_bytes(16)
    set_secret(KEY_NAME, base64.urlsafe_b64encode(salt + derive_key(code, salt)).decode())
    return code


def _stored_key() -> tuple[bytes, bytes]:
    from prometheus.config import get_secret

    raw = get_secret(KEY_NAME)
    if not raw:
        raise BackupError("Backups aren't set up yet: turn them on in Settings (you'll get a recovery code).")
    data = base64.urlsafe_b64decode(raw)
    return data[:16], data[16:]


# -- making the archive ------------------------------------------------------------------------
def _copy_db(db: Path, out: Path) -> None:
    """A consistent copy of the live database (SQLite's own backup, safe while Prometheus is running)."""
    src = sqlite3.connect(str(db))
    dst = sqlite3.connect(str(out))
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def build_zip(data: Path, home: Path, memory_folder: Path | None, out: Path) -> dict:
    """Write the plain archive to `out`. Returns counts for the report."""
    data, home = Path(data), Path(home)
    files = 0
    size = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        tmp_db = out.with_suffix(".db")
        if (data / "memory.db").exists():
            _copy_db(data / "memory.db", tmp_db)
            z.write(tmp_db, "data/memory.db")
            tmp_db.unlink(missing_ok=True)
            files += 1
        for root, dirs, names in os.walk(data):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith((".git", "vosk-model"))]
            for n in names:
                p = Path(root) / n
                if n.startswith("memory.db") or p.suffix in (".part", ".tmp", ".onnx", ".bin", ".pbak"):
                    continue
                try:
                    st = p.stat()
                except OSError:
                    continue
                if st.st_size > MAX_FILE:
                    continue
                z.write(p, "data/" + str(p.relative_to(data)).replace(os.sep, "/"))
                files += 1
                size += st.st_size
        extra = [(memory_folder, "memory-folder")] if memory_folder and memory_folder.exists() else []
        extra += [(home / "Documents" / f, f"documents/{f}") for f in DOC_FOLDERS if (home / "Documents" / f).exists()]
        for folder, prefix in extra:
            for root, _, names in os.walk(folder):
                for n in names:
                    p = Path(root) / n
                    try:
                        if p.stat().st_size > MAX_FILE:
                            continue
                    except OSError:
                        continue
                    z.write(p, f"{prefix}/" + str(p.relative_to(folder)).replace(os.sep, "/"))
                    files += 1
                    size += p.stat().st_size
        z.writestr("backup.json", json.dumps({"made": time.time(), "files": files, "version": 1}))
    return {"files": files, "bytes": size}


# -- encryption: AES-256-GCM in pieces, so big backups never sit whole in memory ------------------
def encrypt_file(src: Path, dst: Path, salt: bytes, key: bytes) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    aes = AESGCM(key)
    prefix = secrets.token_bytes(4)
    with open(src, "rb") as fin, open(dst, "wb") as fout:
        fout.write(MAGIC + salt + prefix)
        n = 0
        while True:
            piece = fin.read(CHUNK)
            last = len(piece) < CHUNK
            nonce = prefix + struct.pack(">Q", n)
            sealed = aes.encrypt(nonce, piece, MAGIC + struct.pack(">Q?", n, last))
            fout.write(struct.pack(">I", len(sealed)) + sealed)
            n += 1
            if last:
                break


def decrypt_file(src: Path, dst: Path, code: str) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    with open(src, "rb") as fin, open(dst, "wb") as fout:
        head = fin.read(len(MAGIC) + 20)
        if not head.startswith(MAGIC):
            raise BackupError("That isn't a Prometheus backup file.")
        salt, prefix = head[len(MAGIC):len(MAGIC) + 16], head[len(MAGIC) + 16:]
        aes = AESGCM(derive_key(code, salt))
        n = 0
        while True:
            size_raw = fin.read(4)
            if len(size_raw) < 4:
                raise BackupError("The backup file is cut short (download it again).")
            sealed = fin.read(struct.unpack(">I", size_raw)[0])
            nonce = prefix + struct.pack(">Q", n)
            for last in (False, True):
                try:
                    piece = aes.decrypt(nonce, sealed, MAGIC + struct.pack(">Q?", n, last))
                    break
                except InvalidTag:
                    piece = None
            if piece is None:
                raise BackupError("Wrong recovery code, or the file was changed.")
            fout.write(piece)
            n += 1
            if last:
                break


# -- Drive --------------------------------------------------------------------------------------
def _folder_id(g, store) -> str:
    fid = store.kv_get("backup_folder_id") or ""
    if fid:
        return fid
    made = g._call("POST", FILES, json={"name": FOLDER, "mimeType": "application/vnd.google-apps.folder"}).json()
    store.kv_set("backup_folder_id", made["id"])
    return made["id"]


def upload(g, store, path: Path, name: str) -> str:
    """Resumable upload in 8 MB pieces. Returns the Drive file id."""
    folder = _folder_id(g, store)
    start = g._call("POST", UPLOAD, params={"uploadType": "resumable"},
                    json={"name": name, "parents": [folder], "mimeType": "application/octet-stream"})
    url = start.headers.get("Location")
    if not url:
        raise BackupError("Google Drive didn't accept the upload.")
    total = path.stat().st_size
    with open(path, "rb") as f:
        sent = 0
        while True:
            piece = f.read(CHUNK)
            end = sent + len(piece) - 1
            headers = {"Authorization": f"Bearer {g._token()}",
                       "Content-Range": f"bytes {sent}-{end}/{total}" if piece else f"bytes */{total}"}
            resp = g.http.put(url, content=piece, headers=headers, timeout=300)
            if resp.status_code in (200, 201):
                return resp.json().get("id", "")
            if resp.status_code != 308:
                raise BackupError(f"Upload stopped (Google said {resp.status_code}).")
            sent += len(piece)
            if not piece:
                raise BackupError("Upload didn't finish.")


def prune(g, store, keep: int = KEEP) -> int:
    folder = _folder_id(g, store)
    files = g._call("GET", FILES, params={"q": f"'{folder}' in parents and trashed = false",
                                          "orderBy": "createdTime desc", "fields": "files(id,name)",
                                          "pageSize": 100}).json().get("files", [])
    old = [f for f in files if f["name"].endswith(".pbak")][keep:]
    for f in old:
        g._call("DELETE", f"{FILES}/{f['id']}")
    return len(old)


def run(services, google=None, kind: str = "laptop") -> str:
    """Make, encrypt and upload tonight's backup. Returns what happened."""
    from prometheus.config import data_dir

    salt, key = _stored_key()
    data = data_dir()
    archive = getattr(services, "archive", None)
    memory_folder = Path(archive.folder) if archive is not None and getattr(archive, "folder", None) else None
    with tempfile.TemporaryDirectory(prefix="prom-backup-") as tmp:
        plain, sealed = Path(tmp) / "backup.zip", Path(tmp) / "backup.pbak"
        counts = build_zip(data, services.home, memory_folder, plain)
        encrypt_file(plain, sealed, salt, key)
        plain.unlink()
        size = sealed.stat().st_size
        name = f"Prometheus {kind} {time.strftime('%Y-%m-%d %H%M')}.pbak"
        if google is None:
            from prometheus.connectors.google import Google

            google = Google()
        upload(google, services.store, sealed, name)
        removed = prune(google, services.store)
    services.store.kv_set("backup_last", json.dumps({"when": time.time(), "files": counts["files"],
                                                     "bytes": size, "name": name}))
    return (f"Backed up {counts['files']} files ({size / 1024 ** 2:.1f} MB, encrypted) to Drive as {name}"
            + (f"; removed {removed} old backup(s)." if removed else "."))


def restore(file: Path, code: str, data: Path, home: Path, overwrite: bool = False) -> str:
    """Put a backup back on this laptop. Prometheus must not be running."""
    data, home = Path(data), Path(home)
    if (data / "memory.db").exists() and not overwrite:
        raise BackupError(f"{data} already has a Prometheus memory. Restore with --overwrite to replace it "
                          "(the current one is kept as memory.db.before-restore).")
    with tempfile.TemporaryDirectory(prefix="prom-restore-") as tmp:
        plain = Path(tmp) / "backup.zip"
        decrypt_file(Path(file), plain, code)
        if (data / "memory.db").exists():
            (data / "memory.db").replace(data / "memory.db.before-restore")
        n = 0
        with zipfile.ZipFile(plain) as z:
            for item in z.infolist():
                name = item.filename
                if name == "backup.json" or name.endswith("/"):
                    continue
                if name.startswith("data/"):
                    target = data / name[5:]
                elif name.startswith("documents/"):
                    target = home / "Documents" / name[10:]
                elif name.startswith("memory-folder/"):
                    from prometheus.memory.archive import FOLDER_NAME

                    target = home / FOLDER_NAME / name[14:]
                else:
                    continue
                target = target.resolve()
                base = (data if name.startswith("data/") else home).resolve()
                if base not in target.parents:
                    raise BackupError("The backup has a file pointing outside its folder; stopped.")
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(item) as src, open(target, "wb") as dst:
                    dst.write(src.read())
                n += 1
    return f"Restored {n} files. Start Prometheus and sign in with your usual password."
