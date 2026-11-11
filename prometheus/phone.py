"""The phone: notifications (web push to the installed app), things sent from the phone, bank SMS, and requests
that need the laptop.

The phone always talks to the cloud copy. Notifications go out from the cloud copy; the laptop hands its own
to the cloud (so a reminder that rings on the laptop also reaches the phone). Requests that need the laptop
(its screen, files, apps or browser) wait in a queue the laptop picks up every 30 seconds while it's on.
"""
from __future__ import annotations

import base64
import json
import re
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS laptop_queue (id INTEGER PRIMARY KEY, chat_title TEXT NOT NULL, text TEXT NOT NULL,
    created REAL NOT NULL, taken REAL);
"""


def _migrate(store) -> None:
    cols = {r["name"] for r in store._rows("PRAGMA table_info(laptop_queue)")}
    if "read_only" not in cols:
        store._write("ALTER TABLE laptop_queue ADD COLUMN read_only INTEGER NOT NULL DEFAULT 1")
VAPID_KEY = "VAPID_PRIVATE"
UPLOADS = "Prometheus uploads"
MAX_UPLOAD = 40 * 1024 * 1024
UPLOAD_TYPES = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".pdf", ".docx", ".pptx", ".xlsx", ".txt", ".md", ".csv",
                ".mp3", ".m4a", ".wav", ".ogg", ".opus", ".mp4", ".webm"}
PHONE_CHAT = "From your phone"
# what only the laptop can do: asked from the phone, these are passed to the laptop
LAPTOP_AGENTS = {"system", "screen", "browser", "files", "file_sorter", "obsidian", "opener", "focus", "health",
                 "clipboard", "reader", "antigravity", "code", "tools"}
# (photos, documents, lectures and homework sent from the phone are handled on the cloud copy, where the file is)


def ready(store) -> None:
    with store._lock:
        store._db.executescript(SCHEMA)
        store._db.commit()
    _migrate(store)


# -- web push ---------------------------------------------------------------------------------
def vapid_keys() -> tuple[str, str]:
    """(private key PEM-less base64, public key for the browser). Made once and kept in the key store."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    from prometheus.config import get_secret, set_secret

    raw = get_secret(VAPID_KEY)
    if raw:
        priv = ec.derive_private_key(int.from_bytes(base64.urlsafe_b64decode(raw + "=="), "big"), ec.SECP256R1())
    else:
        priv = ec.generate_private_key(ec.SECP256R1())
        num = priv.private_numbers().private_value.to_bytes(32, "big")
        raw = base64.urlsafe_b64encode(num).decode().rstrip("=")
        set_secret(VAPID_KEY, raw)
    pub = priv.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return raw, base64.urlsafe_b64encode(pub).decode().rstrip("=")


def subscribe(store, sub: dict) -> None:
    ready(store)
    endpoint = str(sub.get("endpoint", ""))
    if not endpoint.startswith("https://") or len(endpoint) > 1000 or not isinstance(sub.get("keys"), dict):
        raise ValueError("That isn't a push subscription.")
    store._write("INSERT OR REPLACE INTO push_subs (endpoint, data, created) VALUES (?, ?, ?)",
                 (endpoint, json.dumps(sub), time.time()))


def push(store, title: str, text: str, url: str = "/", sender=None) -> int:
    """Send a notification to every phone that signed up. Returns how many got it; dead subscriptions are dropped."""
    ready(store)
    rows = store._rows("SELECT endpoint, data FROM push_subs")
    if not rows:
        return 0
    if sender is None:
        from pywebpush import WebPushException, webpush

        priv, _ = vapid_keys()

        def sender(sub, payload):
            try:
                webpush(sub, payload, vapid_private_key=priv, vapid_claims={"sub": "mailto:prometheus@localhost"},
                        ttl=3600)
                return 201
            except WebPushException as e:
                return getattr(e.response, "status_code", 500) if e.response is not None else 500
    sent = 0
    payload = json.dumps({"title": title[:80], "body": text[:240], "url": url})
    for r in rows:
        status = sender(json.loads(r["data"]), payload)
        if status in (404, 410):
            store._write("DELETE FROM push_subs WHERE endpoint = ?", (r["endpoint"],))
        elif 200 <= status < 300:
            sent += 1
    return sent


# -- things sent from the phone -------------------------------------------------------------------
def save_upload(home: Path, name: str, data: bytes) -> Path:
    suffix = Path(name).suffix.lower()
    if suffix not in UPLOAD_TYPES:
        raise ValueError(f"I can take photos, PDFs, documents and recordings, not {suffix or 'that kind of file'}.")
    if len(data) > MAX_UPLOAD:
        raise ValueError("That file is too big (over 40 MB).")
    sub = "Photos" if suffix in (".jpg", ".jpeg", ".png", ".webp", ".heic") else "Files"
    folder = Path(home) / UPLOADS / sub
    folder.mkdir(parents=True, exist_ok=True)
    stem = re.sub(r"[^\w .-]", "_", Path(name).stem)[:60].strip(" .") or "upload"
    target = folder / f"{time.strftime('%Y-%m-%d %H%M%S')} {stem}{suffix}"
    target.write_bytes(data)
    return target


def what_to_do(path: Path, note: str = "") -> str:
    """The request a shared file turns into, when the owner didn't say."""
    if note.strip():
        return f"{note.strip()}\n(The file is: {path})"
    s = path.suffix.lower()
    if s in (".jpg", ".jpeg", ".png", ".webp", ".heic"):
        return (f"I sent a photo from my phone: {path}. If it's a receipt or bill, add it to my spending; if it's my "
                "homework, check it; otherwise tell me what's in it.")
    if s in (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".mp4", ".webm"):
        return f"Turn this recording into lecture notes: {path}"
    return f"I sent a file from my phone: {path}. Summarise it and ask me what to do with it."


# -- bank SMS ------------------------------------------------------------------------------------------
def sms_to_money(money, sender: str, text: str, when: float | None = None) -> str:
    from prometheus.money import BANKISH, parse_alert

    # Indian bank SMS come from short sender ids like "VM-HDFCBK" or "AX-ICICIT", never from a phone number
    if not (BANKISH.search(sender or "") and re.fullmatch(r"[A-Z]{2}-[A-Z0-9]{3,9}|[A-Z0-9]{3,9}", (sender or "").upper())):
        return "Not a bank message."
    got = parse_alert("", text)
    if not got:
        return "No amount found."
    import hashlib

    ref = f"sms:{sender}:{hashlib.sha256(text.encode()).hexdigest()[:16]}"
    if money.add(got["amount"], got["merchant"], got["kind"], "sms", ref, when, said="upi" if got.get("upi") else ""):
        return f"Added ₹{got['amount']:,.0f} ({got['merchant']})."
    return "Already added."


# -- requests that need the laptop --------------------------------------------------------------------
def queue_for_laptop(store, chat_title: str, text: str, read_only: bool = False) -> int:
    """read_only: it came from another AI app, so the laptop may only read for it, never change anything."""
    ready(store)
    return store._write("INSERT INTO laptop_queue (chat_title, text, created, read_only) VALUES (?, ?, ?, ?)",
                        (chat_title, text[:4000], time.time(), int(read_only)))


def take_jobs(store) -> list[dict]:
    ready(store)
    rows = [dict(r) for r in store._rows("SELECT * FROM laptop_queue WHERE taken IS NULL ORDER BY id LIMIT 10")]
    for r in rows:
        store._write("UPDATE laptop_queue SET taken = ? WHERE id = ?", (time.time(), r["id"]))
    return rows


class LaptopJobs:
    """On the laptop: every 30 seconds, pick up what the phone asked for that needs this laptop, do it, and sync
    the answer straight back."""

    def __init__(self, services, http=None):
        self.services, self.http = services, http
        self._stop = threading.Event()

    def once(self) -> int:
        import httpx

        from prometheus import sync

        s = self.services.settings
        url = (getattr(s, "cloud_url", "") or "").rstrip("/")
        key = sync.token(create=False)
        if not url or not key:
            return 0
        http = self.http or httpx.Client(timeout=20)
        try:
            resp = http.post(f"{url}/api/laptop-jobs", headers={"Authorization": f"Bearer {key}"})
            jobs = resp.json().get("jobs", []) if resp.status_code == 200 else []
        except Exception:
            return 0
        if not jobs:
            return 0
        store = self.services.store
        threads = []
        for job in jobs:
            chat = next((c for c, t in store.chats() if t == job["chat_title"]), None) or store.create_chat(
                job["chat_title"])
            threads.append(self.services.start_task(chat, job["text"], read_only=bool(job.get("read_only", 1))))
        for t in threads:
            t.join(timeout=300)
        if self.services.sync is not None:
            self.services.sync.once()                 # the answers go back to the phone now, not in 10 minutes
        return len(jobs)

    def loop(self) -> None:
        from prometheus.features import running

        while not self._stop.wait(30):
            if not running(self.services.settings, "laptop_jobs"):
                continue
            try:
                self.once()
            except Exception:
                pass

    def start(self) -> None:
        threading.Thread(target=self.loop, name="prometheus-laptop-jobs", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
