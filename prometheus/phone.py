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
            if not bool(job.get("read_only", 1)):
                # the cloud copy is less trusted than the laptop: a job that could change something waits for a
                # one-tap yes here (the Approvals page, and a notification). Reading jobs run as before. (M3 B8)
                self._ask_first(job)
                continue
            threads.append(self.services.start_task(self._chat_for(job), job["text"], read_only=True))
        for t in threads:
            t.join(timeout=300)
        if self.services.sync is not None:
            self.services.sync.once()                 # the answers go back to the phone now, not in 10 minutes
        return len(jobs)

    def _chat_for(self, job: dict) -> int:
        store = self.services.store
        title = str(job.get("chat_title") or PHONE_CHAT)
        return next((c for c, t in store.chats() if t == title), None) or store.create_chat(title)

    def _ask_first(self, job: dict) -> None:
        text = str(job.get("text") or "")[:4000]
        self.services.approvals.request(
            "laptop_job", f"Do this on the laptop, asked from your phone/cloud copy: {text[:200]}",
            {"chat_title": str(job.get("chat_title") or PHONE_CHAT), "text": text})
        try:
            self.services.notify("waiting", f"Your phone asked the laptop to: {text[:80]}. Approve it first.")
        except Exception:
            pass

    def run_approved(self, payload: dict) -> str:
        """The owner said yes to a job from the cloud: now it runs, with the normal safety checks."""
        self.services.start_task(self._chat_for(payload), str(payload.get("text") or ""))
        return "Started on the laptop."

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
        from prometheus.runtime import start_job

        start_job("phone_jobs", "Phone laptop jobs loop", self.loop, name="prometheus-laptop-jobs", service=True)

    def stop(self) -> None:
        self._stop.set()


# -- mobile-mcp phone bridge (M6 6.5, replaces adb control) ---------------------------------------------------
APPS = {
    "whatsapp": "com.whatsapp",
    "youtube": "com.google.android.youtube",
    "spotify": "com.spotify.music",
    "instagram": "com.instagram.android",
    "chrome": "com.android.chrome",
    "gmail": "com.google.android.gm",
    "maps": "com.google.android.apps.maps",
    "google maps": "com.google.android.apps.maps",
    "photos": "com.google.android.apps.photos",
    "drive": "com.google.android.apps.docs",
    "calendar": "com.google.android.calendar",
    "clock": "com.google.android.deskclock",
    "settings": "com.android.settings",
    "phone": "com.google.android.dialer",
    "messages": "com.google.android.apps.messaging",
    "telegram": "org.telegram.messenger",
    "snapchat": "com.snapchat.android",
    "x": "com.twitter.android",
    "twitter": "com.twitter.android",
    "netflix": "com.netflix.mediaclient",
    "paytm": "net.one97.paytm",
    "phonepe": "com.phonepe.app",
    "gpay": "com.google.android.apps.nbu.paisa.user",
    "google pay": "com.google.android.apps.nbu.paisa.user",
    "amazon": "in.amazon.mShop.android.shopping",
    "flipkart": "com.flipkart.android",
    "zomato": "com.application.zomato",
    "swiggy": "in.swiggy.android",
    "uber": "com.ubercab",
    "ola": "com.olacabs.customer",
    "discord": "com.discord",
    "reddit": "com.reddit.frontpage",
    "linkedin": "com.linkedin.android",
    "facebook": "com.facebook.katana",
    "teams": "com.microsoft.teams",
    "classroom": "com.google.android.apps.classroom",
    "keep": "com.google.android.keep",
    "notes": "com.google.android.keep",
    "files": "com.google.android.apps.nbu.files",
    "play store": "com.android.vending",
    "calculator": "com.google.android.calculator",
    "contacts": "com.google.android.contacts",
    "notion": "notion.id",
    "obsidian": "md.obsidian",
}

MCP_KEYS = {
    "back": "BACK",
    "home": "HOME",
    "recents": "APP_SWITCH",
    "enter": "ENTER",
    "volume up": "VOLUME_UP",
    "volume down": "VOLUME_DOWN",
    "delete": "BACKSPACE",
    "backspace": "BACKSPACE",
    "lock": "LOCK",
    "power": "POWER",
    "up": "DPAD_UP",
    "down": "DPAD_DOWN",
    "left": "DPAD_LEFT",
    "right": "DPAD_RIGHT",
    "ok": "DPAD_CENTER",
}

NEVER_OPEN = {"com.termux", "jackpal.androidterm", "com.android.shell"}
NEVER_WORDS = ("termux", "terminal", "androidterm", "adb shell")
PHONE_NO = re.compile(r"^\+?[0-9 ()-]{5,20}$")
PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
_PWD_HINT_RE = re.compile(r"\b(password|passcode|passphrase|pin)\b", re.IGNORECASE)


class PhoneError(Exception):
    pass


def _parse_json_or_lines(text: str):
    txt = (text or "").strip()
    if not txt:
        return None
    for ch in ("[", "{"):
        pos = txt.find(ch)
        if pos != -1:
            try:
                return json.loads(txt[pos:])
            except ValueError:
                pass
    try:
        return json.loads(txt)
    except ValueError:
        return txt


def _normalize_element(raw: dict) -> dict | None:
    label = str(
        raw.get("text")
        or raw.get("label")
        or raw.get("contentDescription")
        or raw.get("content-desc")
        or raw.get("name")
        or ""
    ).strip()
    coords = raw.get("coordinates") or raw.get("center") or {}
    x = coords.get("x", raw.get("x"))
    y = coords.get("y", raw.get("y"))
    if x is not None and y is not None and "width" in coords and "height" in coords:
        x = int(x) + max(0, int(coords["width"])) // 2
        y = int(y) + max(0, int(coords["height"])) // 2
    if (x is None or y is None) and isinstance(raw.get("bounds"), dict):
        b = raw["bounds"]
        if all(k in b for k in ("x", "y", "width", "height")):
            x = int(b["x"]) + int(b["width"]) // 2
            y = int(b["y"]) + int(b["height"]) // 2
        elif all(k in b for k in ("left", "top", "right", "bottom")):
            x = (int(b["left"]) + int(b["right"])) // 2
            y = (int(b["top"]) + int(b["bottom"])) // 2
    if x is None or y is None:
        return None
    cls_name = str(raw.get("className") or raw.get("type") or "").lower()
    ident = str(raw.get("identifier") or raw.get("id") or "")
    is_pwd = bool(raw.get("password") or raw.get("isPassword"))
    if not is_pwd and _PWD_HINT_RE.search(f"{label} {ident}"):
        if any(k in cls_name for k in ("edit", "input", "text", "")):
            is_pwd = True
    return {
        "label": label[:120],
        "x": int(x),
        "y": int(y),
        "ref": str(raw.get("ref") or ""),
        "clickable": bool(raw.get("clickable", True)),
        "password": is_pwd,
        "editable": bool(raw.get("editable") or "edit" in cls_name),
    }


def _parse_elements_text(raw_text: str) -> list[dict]:
    out: list[dict] = []
    for line in (raw_text or "").splitlines():
        ref_m = re.search(r"(@e\d+)", line)
        pos_m = re.search(r"\((\d+)\s*,\s*(\d+)\)", line)
        if not pos_m:
            continue
        x, y = int(pos_m.group(1)), int(pos_m.group(2))
        label = re.sub(r"@e\d+", "", line)
        label = re.sub(r"\(\d+\s*,\s*\d+\)", "", label).strip(" :-|[]")
        out.append({
            "label": (label or (ref_m.group(1) if ref_m else ""))[:120],
            "x": x,
            "y": y,
            "ref": ref_m.group(1) if ref_m else "",
            "clickable": True,
            "password": bool(_PWD_HINT_RE.search(label)),
            "editable": "edit" in line.lower(),
        })
    return out


class MobilePhone:
    """Phone control client delegating to the mobile-mcp server in Other apps (MCP)."""

    def __init__(self, target: str = "", manager=None, conn=None):
        self.target = (target or "").strip()
        self._manager = manager
        self._conn = conn
        self._device_cache = ""

    def _get_conn(self):
        if self._conn is not None:
            return self._conn
        from prometheus.connectors.manager import find_mobile_mcp
        from prometheus.connectors.mcp_client import MCPError

        mgr = self._manager
        if mgr is None:
            from prometheus.agents import apps_agent

            mgr = apps_agent.MANAGER
        try:
            return find_mobile_mcp(mgr)
        except MCPError as e:
            raise PhoneError(str(e)) from e

    @staticmethod
    def _check_stop() -> None:
        """Stop all stops a phone action between its steps (a call is several: dialer, number, Call)."""
        from prometheus.agents import phone_agent

        ks = getattr(getattr(phone_agent, "SERVICES", None), "killswitch", None)
        if ks is not None and ks.is_set:
            raise PhoneError("Stopped: everything is paused (Stop all).")

    def _call(self, tool: str, arguments: dict | None = None) -> str:
        from prometheus.connectors.mcp_client import MCPError

        self._check_stop()
        conn = self._get_conn()
        try:
            return conn.call(tool, arguments or {})
        except MCPError as e:
            raise PhoneError(f"Phone tool {tool} failed: {e}") from e

    def _call_raw(self, tool: str, arguments: dict | None = None) -> dict:
        from prometheus.connectors.mcp_client import MCPError

        self._check_stop()
        conn = self._get_conn()
        try:
            return conn.call_raw(tool, arguments or {})
        except MCPError as e:
            raise PhoneError(f"Phone tool {tool} failed: {e}") from e

    def device_id(self) -> str:
        raw = self._call("mobile_list_available_devices", {})
        parsed = _parse_json_or_lines(raw)
        devices: list[dict] = []
        if isinstance(parsed, dict):
            devices = list(parsed.get("devices") or [])
        elif isinstance(parsed, list):
            devices = list(parsed)
        elif isinstance(parsed, str):
            for line in parsed.splitlines():
                tok = line.strip().split()
                if tok and ("device" in line or "online" in line or len(tok) == 1):
                    devices.append({"id": tok[0], "state": "online"})
        online = [
            d for d in devices
            if isinstance(d, dict) and d.get("id") and str(d.get("state", "online")).lower() in ("online", "device", "")
        ]
        if self.target:
            exact = [d for d in online if str(d.get("id")) == self.target or self.target in str(d.get("id"))]
            if exact:
                self._device_cache = str(exact[0]["id"])
                return self._device_cache
        if online:
            self._device_cache = str(online[0]["id"])
            return self._device_cache
        raise PhoneError(
            "The phone isn't connected right now. Plug in USB or enable Wireless debugging and connect mobile-mcp."
        )

    def ensure_connected(self) -> str:
        return self.device_id()

    def available(self) -> bool:
        try:
            self.device_id()
            return True
        except PhoneError:
            return False

    def packages(self) -> list[dict]:
        dev = self.device_id()
        raw = self._call("mobile_list_apps", {"device": dev})
        parsed = _parse_json_or_lines(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("apps") or parsed.get("packages") or []
        out: list[dict] = []
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str):
                    out.append({"package": item.strip(), "name": ""})
                elif isinstance(item, dict):
                    pkg = str(item.get("packageName") or item.get("package") or item.get("id") or "").strip()
                    name = str(item.get("name") or item.get("label") or "").strip()
                    if pkg:
                        out.append({"package": pkg, "name": name})
        elif isinstance(parsed, str):
            text_body = re.sub(r"^Found these apps on device:\s*", "", parsed.strip(), flags=re.IGNORECASE)
            pairs = list(re.finditer(r"([^,()]+?)\s*\(([A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+)\)", text_body))
            if pairs:
                for m in pairs:
                    out.append({"package": m.group(2).strip(), "name": m.group(1).strip()})
            else:
                for line in text_body.splitlines():
                    m = re.search(r"([a-zA-Z][a-zA-Z0-9_]*\.[a-zA-Z0-9_.]+)", line)
                    if m:
                        out.append({"package": m.group(1), "name": line.strip()})
        return out

    def find_app(self, name: str) -> str:
        low = " ".join(str(name).lower().split())
        if not low:
            raise PhoneError("Which app should I open?")
        if any(bad in low for bad in NEVER_WORDS) or low in NEVER_OPEN:
            raise PhoneError("Prometheus never opens terminal apps on the phone.")
        if PACKAGE.fullmatch(low):
            if low in NEVER_OPEN:
                raise PhoneError("Prometheus never opens terminal apps on the phone.")
            return low
        installed = self.packages()
        words = re.findall(r"[a-z0-9]+", low)
        exact_name = [p["package"] for p in installed if p["name"].lower() == low]
        if exact_name:
            pkg = exact_name[0]
        else:
            matches = [
                p["package"]
                for p in installed
                if all(w in (p["package"] + " " + p["name"]).lower() for w in words)
            ]
            if matches:
                pkg = sorted(matches, key=len)[0]
            elif low in APPS:
                pkg = APPS[low]
            else:
                raise PhoneError(f"I can't find an app called '{name}' on your phone.")
        if pkg in NEVER_OPEN:
            raise PhoneError("Prometheus never opens terminal apps on the phone.")
        return pkg

    def open_app(self, name: str) -> str:
        pkg = self.find_app(name)
        dev = self.device_id()
        self._call("mobile_launch_app", {"device": dev, "packageName": pkg})
        return f"Opened {name} on your phone."

    def open_url(self, url: str) -> str:
        from prometheus.safety.web import check_url

        check_url(url)
        dev = self.device_id()
        self._call("mobile_open_url", {"device": dev, "url": url})
        return "Opened it on your phone."

    def screenshot(self) -> bytes:
        dev = self.device_id()
        raw = self._call_raw("mobile_take_screenshot", {"device": dev})
        for item in raw.get("content", []):
            if item.get("type") == "image" and item.get("data"):
                try:
                    return base64.b64decode(item["data"])
                except Exception as e:
                    raise PhoneError("The phone screenshot wasn't valid image data.") from e
        raise PhoneError("The phone didn't send its screen (is it unlocked?).")

    def elements(self) -> list[dict]:
        dev = self.device_id()
        raw = self._call("mobile_list_elements_on_screen", {"device": dev, "format": "json"})
        parsed = _parse_json_or_lines(raw)
        if isinstance(parsed, dict):
            parsed = parsed.get("elements") or parsed.get("items") or []
        if isinstance(parsed, list):
            out = []
            for item in parsed:
                if isinstance(item, dict):
                    norm = _normalize_element(item)
                    if norm and (norm["label"] or norm["password"]):
                        out.append(norm)
            return out
        return _parse_elements_text(str(parsed or ""))

    def find(self, target: str) -> dict | None:
        want = " ".join(str(target).lower().split())
        els = self.elements()
        exact = [e for e in els if e["label"].lower() == want]
        part = [e for e in els if want in e["label"].lower()]
        pick = exact or part
        if not pick:
            return None
        return sorted(pick, key=lambda e: (not e["clickable"], len(e["label"])))[0]

    def tap(self, x: int, y: int, ref: str = "") -> str:
        if not (0 <= int(x) <= 5000 and 0 <= int(y) <= 5000):
            raise PhoneError("That spot is off the screen.")
        dev = self.device_id()
        args: dict = {"device": dev, "x": int(x), "y": int(y)}
        if ref:
            args["ref"] = ref
        self._call("mobile_click_on_screen_at_coordinates", args)
        return "Tapped."

    def swipe(self, direction: str) -> str:
        d = (direction or "").lower().strip()
        if d not in ("up", "down", "left", "right"):
            raise PhoneError("Swipe up, down, left or right.")
        dev = self.device_id()
        self._call("mobile_swipe_on_screen", {"device": dev, "direction": d})
        return f"Swiped {d}."

    def type_text(self, text: str) -> str:
        from prometheus.safety.secrets import secret_problem

        problem = secret_problem(text)
        if problem:
            raise PhoneError(problem)
        if any(e.get("password") for e in self.elements()):
            raise PhoneError("There's a password box on screen: Prometheus never types passwords. Type it yourself.")
        dev = self.device_id()
        clean = " ".join(str(text).split())[:500]
        if not clean:
            raise PhoneError("Type something first.")
        self._call("mobile_type_keys", {"device": dev, "text": clean, "submit": False})
        return f"Typed {len(clean)} characters."

    def key(self, name: str) -> str:
        k = " ".join(str(name).lower().split())
        btn = MCP_KEYS.get(k)
        if not btn:
            raise PhoneError(f"I can press: {', '.join(sorted(MCP_KEYS))}.")
        dev = self.device_id()
        self._call("mobile_press_button", {"device": dev, "button": btn})
        return f"Pressed {name}."

    @staticmethod
    def validate_setting(what: str, value: str) -> tuple[str, str, bool]:
        w, v = what.lower().strip(), str(value).lower().strip()
        on = v in ("on", "true", "1", "enable", "yes")
        if w in ("wifi", "bluetooth", "dnd", "do not disturb", "rotation", "auto rotate", "mobile data", "data"):
            return w, v, on
        if w == "volume":
            if not v.isdigit() or not 0 <= int(v) <= 15:
                raise PhoneError("Phone volume is 0 to 15.")
            return w, v, True
        if w == "brightness":
            if not v.isdigit() or not 0 <= int(v) <= 100:
                raise PhoneError("Brightness is 0 to 100.")
            return w, v, True
        raise PhoneError("I can change: wifi, bluetooth, dnd, volume (0-15), brightness (0-100), rotation, data.")

    def setting(self, what: str, value: str = "") -> str:
        w, v, on = self.validate_setting(what, value)
        self.open_app("settings")
        labels = {
            "wifi": "wi-fi",
            "bluetooth": "bluetooth",
            "dnd": "do not disturb",
            "do not disturb": "do not disturb",
            "volume": "sound",
            "brightness": "display",
            "rotation": "auto-rotate",
            "auto rotate": "auto-rotate",
            "mobile data": "mobile network",
            "data": "mobile network",
        }
        el = self.find(labels.get(w, w))
        if el is not None:
            self.tap(el["x"], el["y"], el.get("ref", ""))
        return f"Opened Settings for {w} ({v if w in ('volume', 'brightness') else ('on' if on else 'off')})."

    def alarm(self, hour: int, minute: int, label: str = "") -> str:
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise PhoneError("That isn't a time.")
        self.open_app("clock")
        el = self.find("Alarm")
        if el is not None:
            self.tap(el["x"], el["y"], el.get("ref", ""))
        return f"Opened Clock Alarm for {int(hour):02d}:{int(minute):02d}."

    def timer(self, seconds: int) -> str:
        if not 1 <= int(seconds) <= 86400:
            raise PhoneError("A timer is 1 second to 24 hours.")
        self.open_app("clock")
        el = self.find("Timer")
        if el is not None:
            self.tap(el["x"], el["y"], el.get("ref", ""))
        return f"Opened Clock Timer for {int(seconds) // 60} min {int(seconds) % 60} s."

    def contact(self, name: str) -> tuple[str, str]:
        if PHONE_NO.fullmatch(name.strip()):
            return name.strip(), re.sub(r"[^\d+]", "", name)
        raise PhoneError("Give a phone number to call or message via mobile-mcp.")

    def message(self, to: str, text: str, app: str = "whatsapp", who: str = "") -> str:
        from urllib.parse import quote

        name, number = self.contact(to)
        who = who or name
        body = " ".join(str(text).split())[:1000]
        if app == "sms":
            self.open_app("messages")
            return f"Opened Messages for {who}."
        digits = number.lstrip("+")
        if len(digits) == 10:
            digits = "91" + digits
        self.open_url(f"https://wa.me/{digits}?text={quote(body)}")
        send = self.find("Send")
        if send is None:
            return f"The message to {who} is ready on your phone; tap Send there."
        self.tap(send["x"], send["y"], send.get("ref", ""))
        return f"Sent to {who} (WhatsApp): {body}"

    def call(self, to: str) -> str:
        who, number = self.contact(to)
        self.open_app("phone")
        self.type_text(number)
        btn = self.find("Call")
        if btn is not None:
            self.tap(btn["x"], btn["y"], btn.get("ref", ""))
        return f"Calling {who}."

    def notifications(self) -> list[dict]:
        els = self.elements()
        items = [{"app": "screen", "title": e["label"], "text": ""} for e in els if e.get("label")][:20]
        return items

    def battery(self) -> str:
        for e in self.elements():
            m = re.search(r"(\d{1,3})%", e.get("label") or "")
            if m:
                return f"Phone battery {m.group(1)}%."
        return "Could not see the battery percentage on screen."

