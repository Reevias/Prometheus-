"""Controlling the owner's Android phone through wireless debugging (ADB): a fixed list of actions, no free
command line. The phone is paired once (Developer options → Wireless debugging → Pair device with pairing code)
and reached over the private Tailscale link, so it works from the cloud copy too.

Every input that reaches the phone is checked: app names and package ids, phone numbers, times, levels and URLs
have strict formats, and typed text is escaped for the phone's `input` command and never passed through a shell.
"""
from __future__ import annotations

import re
import shlex
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET

PACKAGE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")
TARGET = re.compile(r"^[A-Za-z0-9.:\[\]-]{3,80}$")
PHONE_NO = re.compile(r"^\+?[0-9 ()-]{5,20}$")
APPS = {"whatsapp": "com.whatsapp", "youtube": "com.google.android.youtube", "spotify": "com.spotify.music",
        "instagram": "com.instagram.android", "chrome": "com.android.chrome", "gmail": "com.google.android.gm",
        "maps": "com.google.android.apps.maps", "google maps": "com.google.android.apps.maps",
        "photos": "com.google.android.apps.photos", "drive": "com.google.android.apps.docs",
        "calendar": "com.google.android.calendar", "clock": "com.google.android.deskclock",
        "camera": "android.media.action.STILL_IMAGE_CAMERA", "settings": "com.android.settings",
        "phone": "com.google.android.dialer", "messages": "com.google.android.apps.messaging",
        "telegram": "org.telegram.messenger", "snapchat": "com.snapchat.android", "x": "com.twitter.android",
        "twitter": "com.twitter.android", "netflix": "com.netflix.mediaclient", "paytm": "net.one97.paytm",
        "phonepe": "com.phonepe.app", "gpay": "com.google.android.apps.nbu.paisa.user",
        "google pay": "com.google.android.apps.nbu.paisa.user", "amazon": "in.amazon.mShop.android.shopping",
        "flipkart": "com.flipkart.android", "zomato": "com.application.zomato", "swiggy": "in.swiggy.android",
        "uber": "com.ubercab", "ola": "com.olacabs.customer", "discord": "com.discord", "reddit": "com.reddit.frontpage",
        "linkedin": "com.linkedin.android", "facebook": "com.facebook.katana", "teams": "com.microsoft.teams",
        "classroom": "com.google.android.apps.classroom", "keep": "com.google.android.keep",
        "files": "com.google.android.apps.nbu.files", "play store": "com.android.vending", "calculator":
        "com.google.android.calculator", "contacts": "com.google.android.contacts", "notion": "notion.id",
        "obsidian": "md.obsidian", "prometheus": "com.android.chrome"}
KEYS = {"back": "4", "home": "3", "recents": "187", "enter": "66", "volume up": "24", "volume down": "25",
        "mute": "164", "play": "85", "pause": "85", "next": "87", "previous": "88", "lock": "26", "power": "26",
        "search": "84", "delete": "67", "tab": "61", "camera": "27", "wake": "224"}
NEVER_OPEN = {"com.termux", "jackpal.androidterm", "com.android.shell"}


class PhoneError(Exception):
    pass


def _run(args: list[str], timeout: int = 30) -> tuple[int, bytes]:
    proc = subprocess.run(args, capture_output=True, timeout=timeout)
    return proc.returncode, proc.stdout + proc.stderr


def type_escape(text: str) -> str:
    """Text for `adb shell input text`: spaces become %s, shell-special characters are escaped; one line only."""
    text = " ".join(str(text).split())[:500]
    out = []
    for ch in text:
        if ch == " ":
            out.append("%s")
        elif ch in "\\'\"`$&|;<>()*?~#![]{}^%":
            out.append("\\" + ch)
        elif ord(ch) < 32:
            continue
        else:
            out.append(ch)
    return "".join(out)


class Phone:
    def __init__(self, target: str = "", adb: str = "", run=_run):
        self.target = target
        self.adb = adb or shutil.which("adb") or "adb"
        self.run = run

    # -- the link -------------------------------------------------------------------------------
    def _cmd(self, *args: str, timeout: int = 30) -> str:
        if not self.target:
            raise PhoneError("Your phone isn't paired yet: Settings → Phone control.")
        try:
            code, out = self.run([self.adb, "-s", self.target, *args], timeout)
        except FileNotFoundError as e:
            raise PhoneError("The phone tool (adb) isn't installed. The cloud setup installs it; on Windows run "
                             "the installer again.") from e
        except subprocess.TimeoutExpired as e:
            raise PhoneError("The phone didn't answer in time (is it on, with Tailscale and wireless debugging on?)."
                             ) from e
        text = out.decode("utf-8", errors="replace")
        if code != 0 or "device offline" in text or "device '" in text and "not found" in text:
            raise PhoneError("The phone isn't connected right now. Open Settings → Phone control → Connect "
                             "(wireless debugging changes its port after a restart).")
        return text

    def shell(self, *parts: str, timeout: int = 30) -> str:
        """One fixed command on the phone; every part is quoted, so nothing is read as shell syntax."""
        return self._cmd("shell", " ".join(shlex.quote(p) for p in parts), timeout=timeout)

    def pair(self, target: str, code: str) -> str:
        if not TARGET.fullmatch(target or "") or not re.fullmatch(r"\d{6}", code or ""):
            raise PhoneError("Pairing needs the address shown on the phone (like 100.64.1.2:37123) and its 6 digits.")
        _, out = self.run([self.adb, "pair", target, code], 30)
        text = out.decode("utf-8", errors="replace")
        if "Successfully paired" not in text:
            raise PhoneError("Pairing didn't work: check the code and address (they change each time).")
        return "Paired."

    def connect(self, target: str) -> str:
        if not TARGET.fullmatch(target or ""):
            raise PhoneError("The address looks like 100.64.1.2:41234 (IP and port from Wireless debugging).")
        _, out = self.run([self.adb, "connect", target], 30)
        text = out.decode("utf-8", errors="replace")
        if "connected to" not in text:
            raise PhoneError("Couldn't connect: is wireless debugging on, and is the port the one shown now?")
        self.target = target
        return f"Connected to your phone ({self.model()})."

    def model(self) -> str:
        try:
            return self.shell("getprop", "ro.product.model").strip() or "Android"
        except PhoneError:
            return "Android"

    # -- apps and links ---------------------------------------------------------------------------------
    def packages(self) -> list[str]:
        return [l.split(":", 1)[1].strip() for l in self.shell("pm", "list", "packages", "-3").splitlines()
                if l.startswith("package:")] + [l.split(":", 1)[1].strip() for l in
                                                self.shell("pm", "list", "packages", "-s").splitlines()
                                                if l.startswith("package:")]

    def find_app(self, name: str) -> str:
        low = " ".join(str(name).lower().split())
        if PACKAGE.fullmatch(low):
            return low
        if low in APPS:
            return APPS[low]
        words = re.findall(r"[a-z0-9]+", low)
        best = [p for p in self.packages() if all(w in p.lower() for w in words)]
        if not best:
            raise PhoneError(f"I can't find an app called '{name}' on your phone.")
        return sorted(best, key=len)[0]

    def open_app(self, name: str) -> str:
        pkg = self.find_app(name)
        if pkg in NEVER_OPEN:
            raise PhoneError("Prometheus never opens terminal apps on the phone.")
        if pkg.startswith("android.media.action."):
            self.shell("am", "start", "-a", pkg)
        else:
            out = self.shell("monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
            if "No activities found" in out:
                raise PhoneError(f"'{name}' can't be opened directly.")
        return f"Opened {name} on your phone."

    def open_url(self, url: str) -> str:
        from prometheus.safety.web import check_url

        check_url(url)
        self.shell("am", "start", "-a", "android.intent.action.VIEW", "-d", url)
        return "Opened it on your phone."

    # -- the screen -------------------------------------------------------------------------------------
    def screenshot(self) -> bytes:
        if not self.target:
            raise PhoneError("Your phone isn't paired yet: Settings → Phone control.")
        code, out = self.run([self.adb, "-s", self.target, "exec-out", "screencap", "-p"], 30)
        if code != 0 or not out.startswith(b"\x89PNG"):
            raise PhoneError("The phone didn't send its screen (is it unlocked?).")
        return out

    def elements(self) -> list[dict]:
        """What's on screen: each button, text and box with its label and position (from Android itself)."""
        self.shell("uiautomator", "dump", "/sdcard/prometheus-ui.xml", timeout=20)
        xml = self.shell("cat", "/sdcard/prometheus-ui.xml")
        self.shell("rm", "-f", "/sdcard/prometheus-ui.xml")
        try:
            root = ET.fromstring(xml[xml.find("<?xml"):] if "<?xml" in xml else xml)
        except ET.ParseError:
            return []
        out = []
        for n in root.iter("node"):
            label = (n.get("text") or n.get("content-desc") or "").strip()
            m = re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", n.get("bounds") or "")
            if not label or not m:
                continue
            x1, y1, x2, y2 = map(int, m.groups())
            out.append({"label": label[:120], "x": (x1 + x2) // 2, "y": (y1 + y2) // 2,
                        "clickable": n.get("clickable") == "true", "password": n.get("password") == "true",
                        "editable": "EditText" in (n.get("class") or "")})
        return out

    def find(self, target: str) -> dict | None:
        want = " ".join(str(target).lower().split())
        els = self.elements()
        exact = [e for e in els if e["label"].lower() == want]
        part = [e for e in els if want in e["label"].lower()]
        pick = (exact or part)
        if not pick:
            return None
        return sorted(pick, key=lambda e: (not e["clickable"], len(e["label"])))[0]

    def tap(self, x: int, y: int) -> str:
        if not (0 <= int(x) <= 5000 and 0 <= int(y) <= 5000):
            raise PhoneError("That spot is off the screen.")
        self.shell("input", "tap", str(int(x)), str(int(y)))
        return "Tapped."

    def swipe(self, direction: str) -> str:
        size = re.search(r"(\d+)x(\d+)", self.shell("wm", "size")) or None
        w, h = (int(size.group(1)), int(size.group(2))) if size else (1080, 2400)
        cx, cy = w // 2, h // 2
        moves = {"up": (cx, int(h * .75), cx, int(h * .25)), "down": (cx, int(h * .25), cx, int(h * .75)),
                 "left": (int(w * .8), cy, int(w * .2), cy), "right": (int(w * .2), cy, int(w * .8), cy)}
        if direction not in moves:
            raise PhoneError("Swipe up, down, left or right.")
        self.shell("input", "swipe", *map(str, moves[direction]), "300")
        return f"Swiped {direction}."

    def type_text(self, text: str) -> str:
        focused = [e for e in self.elements() if e["password"]]
        if focused:
            raise PhoneError("There's a password box on screen: Prometheus never types passwords. Type it yourself.")
        self._cmd("shell", "input text " + type_escape(text))
        return f"Typed {len(text)} characters."

    def key(self, name: str) -> str:
        code = KEYS.get(str(name).lower().strip())
        if code is None:
            raise PhoneError(f"I can press: {', '.join(sorted(KEYS))}.")
        self.shell("input", "keyevent", code)
        return f"Pressed {name}."

    # -- settings ---------------------------------------------------------------------------------------
    def setting(self, what: str, value: str = "") -> str:
        what, value = what.lower().strip(), str(value).lower().strip()
        on = value in ("on", "true", "1", "enable", "yes")
        if what == "wifi":
            self.shell("cmd", "wifi", "set-wifi-enabled", "enabled" if on else "disabled")
        elif what == "bluetooth":
            self.shell("cmd", "bluetooth_manager", "enable" if on else "disable")
        elif what in ("dnd", "do not disturb"):
            self.shell("cmd", "notification", "set_dnd", "priority" if on else "off")
        elif what == "volume":
            n = int(value) if value.isdigit() else -1
            if not 0 <= n <= 15:
                raise PhoneError("Phone volume is 0 to 15.")
            self.shell("cmd", "media_session", "volume", "--stream", "3", "--set", str(n))
        elif what == "brightness":
            n = int(value) if value.isdigit() else -1
            if not 0 <= n <= 100:
                raise PhoneError("Brightness is 0 to 100.")
            self.shell("settings", "put", "system", "screen_brightness_mode", "0")
            self.shell("settings", "put", "system", "screen_brightness", str(round(n * 2.55)))
        elif what in ("rotation", "auto rotate"):
            self.shell("settings", "put", "system", "accelerometer_rotation", "1" if on else "0")
        elif what in ("mobile data", "data"):
            self.shell("svc", "data", "enable" if on else "disable")
        else:
            raise PhoneError("I can change: wifi, bluetooth, dnd, volume (0-15), brightness (0-100), rotation, data.")
        return f"Phone {what} {'set to ' + value if what in ('volume', 'brightness') else ('on' if on else 'off')}."

    def alarm(self, hour: int, minute: int, label: str = "") -> str:
        if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
            raise PhoneError("That isn't a time.")
        args = ["am", "start", "-a", "android.intent.action.SET_ALARM", "--ei", "android.intent.extra.alarm.HOUR",
                str(int(hour)), "--ei", "android.intent.extra.alarm.MINUTES", str(int(minute)), "--ez",
                "android.intent.extra.alarm.SKIP_UI", "true"]
        if label:
            args += ["--es", "android.intent.extra.alarm.MESSAGE", re.sub(r"[^\w ,.!?-]", "", label)[:60]]
        self.shell(*args)
        return f"Alarm set on your phone for {int(hour):02d}:{int(minute):02d}."

    def timer(self, seconds: int) -> str:
        if not 1 <= int(seconds) <= 86400:
            raise PhoneError("A timer is 1 second to 24 hours.")
        self.shell("am", "start", "-a", "android.intent.action.SET_TIMER", "--ei", "android.intent.extra.alarm.LENGTH",
                   str(int(seconds)), "--ez", "android.intent.extra.alarm.SKIP_UI", "true")
        return f"Timer set on your phone for {int(seconds) // 60} min {int(seconds) % 60} s."

    # -- people -------------------------------------------------------------------------------------------
    def contact(self, name: str) -> tuple[str, str]:
        """(display name, number) for a contact, or a number given directly."""
        if PHONE_NO.fullmatch(name.strip()):
            return name.strip(), re.sub(r"[^\d+]", "", name)
        rows = self.shell("content", "query", "--uri", "content://com.android.contacts/data/phones",
                          "--projection", "display_name:data1")
        want = name.lower().strip()
        found = []
        for line in rows.splitlines():
            m = re.search(r"display_name=(.*?), data1=(.*)$", line)
            if m and want in m.group(1).lower():
                found.append((m.group(1).strip(), re.sub(r"[^\d+]", "", m.group(2))))
        if not found:
            raise PhoneError(f"I can't find {name} in your phone's contacts.")
        exact = [f for f in found if f[0].lower() == want]
        return (exact or found)[0]

    def message(self, to: str, text: str, app: str = "whatsapp", who: str = "") -> str:
        """Opens the message ready to send and presses Send (only after the owner's yes)."""
        from urllib.parse import quote

        name, number = self.contact(to)
        who = who or name
        body = " ".join(str(text).split())[:1000]
        if app == "sms":
            self.shell("am", "start", "-a", "android.intent.action.SENDTO", "-d", f"sms:{number}", "--es",
                       "sms_body", body)
        else:
            digits = number.lstrip("+")
            if len(digits) == 10:
                digits = "91" + digits                 # an Indian number without the country code
            self.shell("am", "start", "-a", "android.intent.action.VIEW", "-d",
                       f"https://wa.me/{digits}?text={quote(body)}")
        time.sleep(3)
        send = self.find("Send")
        if send is None:
            return f"The message to {who} is ready on your phone; tap Send there (I couldn't find the button)."
        self.tap(send["x"], send["y"])
        return f"Sent to {who} ({'SMS' if app == 'sms' else 'WhatsApp'}): {body}"

    def call(self, to: str) -> str:
        who, number = self.contact(to)
        self.shell("am", "start", "-a", "android.intent.action.CALL", "-d", f"tel:{number}")
        return f"Calling {who}."

    # -- what's going on -------------------------------------------------------------------------------------
    def notifications(self) -> list[dict]:
        out = self.shell("dumpsys", "notification", "--noredaction", timeout=30)
        items, cur = [], {}
        for line in out.splitlines():
            line = line.strip()
            m = re.match(r"NotificationRecord\(.*pkg=(\S+)", line)
            if m:
                if cur.get("title") or cur.get("text"):
                    items.append(cur)
                cur = {"app": m.group(1)}
            m = re.match(r"android\.title=\S+ \((.*)\)$", line)
            if m and cur is not None:
                cur["title"] = m.group(1)[:100]
            m = re.match(r"android\.text=\S+ \((.*)\)$", line)
            if m and cur is not None:
                cur["text"] = m.group(1)[:200]
        if cur.get("title") or cur.get("text"):
            items.append(cur)
        return [i for i in items if not i["app"].startswith(("android", "com.android.systemui"))][:20]

    def battery(self) -> str:
        out = self.shell("dumpsys", "battery")
        level = re.search(r"level: (\d+)", out)
        charging = re.search(r"(AC|USB|Wireless) powered: true", out)
        return f"Phone battery {level.group(1) if level else '?'}%" + (", charging." if charging else ".")
