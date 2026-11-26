"""Control the laptop from the phone (M6 6.4b).

The phone talks straight to the laptop, only over the private link (Tailscale or this laptop itself), and only with
a device key made when the owner paired it. Every rule is code on the laptop:

- Pairing: the owner makes a one-time code on the laptop (5 minutes, 5 wrong tries kill it). The phone swaps it for
  a device key; the laptop keeps only a hash of that key.
- Every request needs a paired device key, is refused outside the private link (and when it arrived through a proxy),
  and works only with the Features switch on and not while Stop all is on.
- Asking never runs anything. The phone gets a one-time "yes token"; a separate confirm call with that token runs the
  request (once, within 2 minutes, with the values that were asked: confirm can't change them). Showing the question
  and getting the tap is the phone app's job; the laptop makes sure nothing runs without that second call.
- The 6.4a mouse and keyboard actions (click, type, keys, switch app) then also wait for a yes on the laptop
  (Approvals page and a notification).
- Every step goes in the activity log, never the typed text or any key.
Windows behaviour is the existing laptop.py and pc_control code; here it is only called.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Callable

from prometheus.auth import LoginLimiter

PRIVATE_NETS = tuple(ipaddress.ip_network(n) for n in (
    "100.64.0.0/10",              # Tailscale (IPv4)
    "fd7a:115c:a1e0::/48",        # Tailscale (IPv6)
    "127.0.0.0/8", "::1/128"))    # this laptop itself
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host", "x-forwarded-proto")
CODE_TTL = 300
CODE_TRIES = 5
CONFIRM_TTL = 120
LAPTOP_TTL = 600
MAX_DEVICES = 5
KEEP_REQUESTS = 60
DEVICES_KEY = "remote_devices"
APPROVAL_KIND = "remote_action"
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class RemoteError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message, self.status = message, status


def private_link_ok(host: str, headers) -> bool:
    """True only for the private link: Tailscale addresses or this laptop, and not through a proxy."""
    if any(h in {k.lower() for k in headers} for h in PROXY_HEADERS):
        return False
    if host == "testclient":                           # Starlette's test client (same rule as the sync key check)
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in PRIVATE_NETS)


# -- the actions ----------------------------------------------------------------------------------------------------------
def _pc_read(payload: dict, killswitch=None) -> str:
    from prometheus.pc_control.agent import _execute_read

    return _execute_read(payload, killswitch=killswitch)


def _pc_input(payload: dict, killswitch=None) -> str:
    from prometheus.pc_control.agent import _execute_input

    return _execute_input(payload, killswitch=killswitch)


PC_READ, PC_INPUT = _pc_read, _pc_input


def _int(name: str, lo: int, hi: int, required: bool = True, default=None):
    def check(value):
        if value is None:
            if required:
                raise RemoteError(f"'{name}' is needed.", 422)
            return default
        if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().lstrip("-").isdigit():
            raise RemoteError(f"'{name}' must be a whole number.", 422)
        n = int(str(value).strip())
        if not lo <= n <= hi:
            raise RemoteError(f"'{name}' must be from {lo} to {hi}.", 422)
        return n
    return check


def _choice(name: str, options: tuple, required: bool = True, default=None):
    def check(value):
        if value is None and not required:
            return default
        if value not in options:
            raise RemoteError(f"'{name}' must be one of: {', '.join(options)}.", 422)
        return value
    return check


def _bool(name: str):
    def check(value):
        if not isinstance(value, bool):
            raise RemoteError(f"'{name}' must be true or false.", 422)
        return value
    return check


def _text(name: str, lo: int, hi: int, required: bool = True):
    def check(value):
        if value is None and not required:
            return ""
        if not isinstance(value, str) or not lo <= len(value.strip() if lo else value) <= hi:
            raise RemoteError(f"'{name}' must be text of {lo} to {hi} characters.", 422)
        return value
    return check


def _app(value):
    from prometheus.laptop import LaptopError, _app_name

    try:
        return _app_name(value)
    except LaptopError as e:
        raise RemoteError(str(e), 422) from e


@dataclass
class Action:
    title: str
    params: dict                                   # name -> checker
    run: Callable                                  # (services, params, payload) -> text
    dangerous: bool = False                        # also needs a yes on the laptop (6.4a mouse and keyboard)
    pc_name: str = ""                              # the 6.4a input action this is


def _laptop(services):
    if getattr(services, "laptop", None) is None:
        raise RemoteError("Laptop actions aren't available here.", 409)
    return services.laptop


def _show(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, default=str)[:2000]


def _lp(method: str, *names: str):
    return lambda services, p, payload: _show(getattr(_laptop(services), method)(*[p[n] for n in names]))


def _data_dir() -> str:
    from prometheus.config import data_dir

    return str(data_dir())


ACTIONS: dict[str, Action] = {
    "battery": Action("Battery level", {}, _lp("battery")),
    "wifi_status": Action("Wi-Fi status", {}, _lp("wifi_status")),
    "bluetooth": Action("Bluetooth on, off or status", {"want": _choice("want", ("On", "Off", "status"))},
                        _lp("bluetooth", "want")),
    "volume": Action("Set the volume", {"level": _int("level", 0, 100)}, _lp("volume", "level")),
    "mute": Action("Mute", {}, _lp("mute")),
    "brightness": Action("Set the brightness", {"level": _int("level", 0, 100)}, _lp("brightness", "level")),
    "dark_mode": Action("Dark mode on or off", {"mode": _choice("mode", ("on", "off"))}, _lp("dark_mode", "mode")),
    "do_not_disturb": Action("Do not disturb", {"on": _bool("on")}, _lp("do_not_disturb", "on")),
    "apps_running": Action("Open apps", {}, _lp("apps_running")),
    "app_open": Action("Open an app", {"name": _app}, _lp("app_open", "name")),
    "app_close": Action("Close an app", {"name": _app}, _lp("app_close", "name")),
    "lock": Action("Lock the screen", {}, lambda services, p, payload: _show(_laptop(services).power("lock"))),
    "screenshot": Action("Screenshot (saved on the laptop)", {},
                         lambda s, p, payload: PC_READ({"action": "screenshot", "data_dir": _data_dir()}, s.killswitch)),
    "read_controls": Action("Read the controls of a window", {"window_title": _text("window_title", 0, 100, False),
                                                              "limit": _int("limit", 1, 80, False, 40)},
                            lambda s, p, payload: PC_READ({"action": "read_controls", "data_dir": _data_dir(),
                                                           "window_title": p["window_title"], "limit": p["limit"]},
                                                          s.killswitch)),
    "list_windows": Action("List the open windows", {},
                           lambda s, p, payload: PC_READ({"action": "list_windows", "data_dir": _data_dir()}, s.killswitch)),
    "click": Action("Click the mouse", {"x": _int("x", 0, 20000), "y": _int("y", 0, 20000),
                                        "button": _choice("button", ("left", "right", "middle"), False, "left"),
                                        "clicks": _int("clicks", 1, 3, False, 1)},
                    lambda s, p, payload: PC_INPUT({**payload, "data_dir": _data_dir()}, s.killswitch), True, "click"),
    "type": Action("Type text", {"text": _text("text", 1, 500)},
                   lambda s, p, payload: PC_INPUT({**payload, "data_dir": _data_dir()}, s.killswitch), True, "type"),
    "press_keys": Action("Press keys", {"keys": _text("keys", 1, 40)},
                         lambda s, p, payload: PC_INPUT({**payload, "data_dir": _data_dir()}, s.killswitch), True,
                         "press_keys"),
    "switch_app": Action("Switch to an app", {"app": _text("app", 1, 80)},
                         lambda s, p, payload: PC_INPUT({**payload, "data_dir": _data_dir()}, s.killswitch), True,
                         "switch_app"),
}


def clean_params(action: Action, raw) -> dict:
    if not isinstance(raw, dict):
        raise RemoteError("The values must be an object.", 422)
    extra = set(raw) - set(action.params)
    if extra:
        raise RemoteError(f"Unknown value: {sorted(extra)[0]}.", 422)
    return {name: check(raw.get(name)) for name, check in action.params.items()}


# -- the controller ---------------------------------------------------------------------------------------------------------
def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


class RemoteControl:
    def __init__(self, services, clock: Callable[[], float] = time.time):
        self.services, self.clock = services, clock
        self.limiter = LoginLimiter()
        self._code: dict | None = None
        self._requests: dict[str, dict] = {}
        self._lock = threading.RLock()

    # -- logging --------------------------------------------------------------------------------------------------------
    def _log(self, stage: str, **detail) -> None:
        try:
            self.services.store.audit("phone", f"remote_{stage}", detail)
        except Exception:                                   # noqa: BLE001 - logging never stops a refusal
            pass

    @staticmethod
    def _what(req: dict) -> dict:
        """What the log says about a request: never the typed text."""
        out = {"request": req["id"], "action": req["action"], "device": req["device_name"], "risk": req["risk"]}
        if req["action"] == "type":
            out["text"] = f"{len(req['params'].get('text', ''))} characters (not logged)"
        else:
            out.update({k: v for k, v in req["params"].items() if isinstance(v, (str, int, bool))})
        return out

    def refused(self, why: str, client: str = "", path: str = "") -> None:
        self._log("refused", why=why, client=client[:64], path=path[:80])

    # -- guards ----------------------------------------------------------------------------------------------------------
    def stopped(self) -> bool:
        s = self.services
        return bool(s.killswitch.is_set or getattr(s.settings, "paused", False))

    def enabled(self) -> bool:
        from prometheus.features import on

        return on(self.services.settings, "remote_control")

    # -- pairing and devices ------------------------------------------------------------------------------------------------
    def _devices(self) -> dict:
        d = self.services.store.kv_get(DEVICES_KEY, {})
        return d if isinstance(d, dict) else {}

    def devices(self) -> list[dict]:
        return [{"id": i, "name": d["name"], "created": d["created"], "last_seen": d.get("last_seen", 0.0)}
                for i, d in self._devices().items()]

    def new_pairing_code(self) -> dict:
        if not self.enabled():
            raise RemoteError("Switch on \"Control the laptop from the phone\" in Settings → Features first.", 409)
        with self._lock:
            code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
            self._code = {"code": code, "until": self.clock() + CODE_TTL, "tries": 0}
        self._log("code", note="a pairing code was made")
        return {"code": code, "expires_in": CODE_TTL}

    def pair(self, code: str, name: str) -> dict:
        with self._lock:
            c = self._code
            if not c or self.clock() > c["until"]:
                self._code = None
                raise RemoteError("No pairing code is active. Make a new one on the laptop.", 403)
            if not hmac.compare_digest(str(code or "").strip().upper().encode(), c["code"].encode()):
                c["tries"] += 1
                if c["tries"] >= CODE_TRIES:
                    self._code = None
                raise RemoteError("That code is wrong.", 403)
            self._code = None                                     # a code works once
            devices = self._devices()
            if len(devices) >= MAX_DEVICES:
                raise RemoteError(f"{MAX_DEVICES} phones are already paired. Remove one on the laptop first.", 409)
            device_id, secret = secrets.token_hex(4), secrets.token_urlsafe(32)
            clean = " ".join(str(name or "Phone").split())[:40] or "Phone"
            devices[device_id] = {"name": clean, "hash": _hash(secret), "created": self.clock(), "last_seen": 0.0}
            self.services.store.kv_set(DEVICES_KEY, devices)
        self._log("pair", device=clean, device_id=device_id)
        return {"device_id": device_id, "key": f"{device_id}.{secret}", "name": clean}

    def revoke(self, device_id: str) -> bool:
        with self._lock:
            devices = self._devices()
            gone = devices.pop(device_id, None)
            if gone is None:
                return False
            self.services.store.kv_set(DEVICES_KEY, devices)
            for r in self._requests.values():
                if r["device"] == device_id and r["state"] in ("waiting_phone", "waiting_laptop"):
                    r["state"], r["confirm"] = "cancelled", None
        self._log("revoke", device=gone["name"], device_id=device_id)
        return True

    def authenticate(self, key: str) -> dict | None:
        device_id, _, secret = str(key or "").partition(".")
        d = self._devices().get(device_id)
        if not d or not secret or not hmac.compare_digest(_hash(secret).encode(), d["hash"].encode()):
            return None
        now = self.clock()
        if now - float(d.get("last_seen", 0)) > 60:
            with self._lock:
                devices = self._devices()
                if device_id in devices:
                    devices[device_id]["last_seen"] = now
                    self.services.store.kv_set(DEVICES_KEY, devices)
        return {"id": device_id, "name": d["name"]}

    # -- the list ----------------------------------------------------------------------------------------------------------
    def catalog(self) -> list[dict]:
        return [{"id": k, "title": a.title, "params": {n: getattr(c, "__name__", "value") for n, c in a.params.items()},
                 "asks_on_phone": True, "needs_laptop_yes": a.dangerous} for k, a in ACTIONS.items()]

    # -- asking, confirming, running -------------------------------------------------------------------------------------
    def _view(self, req: dict, with_confirm: bool = False) -> dict:
        out = {"id": req["id"], "action": req["action"], "summary": req["summary"], "state": req["state"],
               "needs_laptop_yes": req["risk"] == "dangerous", "result": req.get("result", ""),
               "error": req.get("error", "")}
        if with_confirm:
            out["confirm"], out["expires_in"] = req["confirm"], CONFIRM_TTL
        return out

    def _get(self, device: dict, request_id: str) -> dict:
        req = self._requests.get(request_id)
        if req is None or req["device"] != device["id"]:
            raise RemoteError("No such request.", 404)
        now = self.clock()
        if req["state"] == "waiting_phone" and now > req["created"] + CONFIRM_TTL:
            req["state"], req["confirm"] = "expired", None
            self._log("expired", **self._what(req))
        elif req["state"] == "waiting_laptop" and now > req["laptop_since"] + LAPTOP_TTL:
            req["state"] = "expired"
            self._log("expired", **self._what(req))
        return req

    def propose(self, device: dict, action_id: str, raw) -> dict:
        action = ACTIONS.get(action_id)
        if action is None:
            raise RemoteError("I can't do that from the phone.", 404)
        params = clean_params(action, raw)
        summary, payload = action.title, {}
        if action.pc_name:
            from prometheus.pc_control.agent import _describe_input
            from prometheus.pc_control.errors import PCControlError

            try:
                summary, payload = _describe_input(action.pc_name, params)
            except (PCControlError, ValueError) as e:
                raise RemoteError(str(e), 422) from e
        else:
            shown = ", ".join(f"{k}={v}" for k, v in params.items() if v not in ("", None))
            summary = f"{action.title}" + (f" ({shown})" if shown else "")
        with self._lock:
            req = {"id": secrets.token_hex(6), "device": device["id"], "device_name": device["name"],
                   "action": action_id, "params": params, "payload": payload, "summary": summary,
                   "risk": "dangerous" if action.dangerous else "normal", "state": "waiting_phone",
                   "created": self.clock(), "confirm": secrets.token_urlsafe(16), "laptop_since": 0.0}
            self._requests[req["id"]] = req
            for old in sorted(self._requests.values(), key=lambda r: r["created"])[:-KEEP_REQUESTS]:
                self._requests.pop(old["id"], None)
        self._log("proposed", **self._what(req))
        return self._view(req, with_confirm=True)

    def confirm(self, device: dict, request_id: str, token: str) -> dict:
        with self._lock:
            req = self._get(device, request_id)
            if req["state"] == "expired":
                raise RemoteError("That question is too old. Ask again.", 410)
            if req["state"] != "waiting_phone" or not req["confirm"]:
                raise RemoteError("That request isn't waiting for a yes any more.", 409)
            if not hmac.compare_digest(str(token or "").encode(), req["confirm"].encode()):
                raise RemoteError("That isn't the yes for this request.", 403)
            req["confirm"] = None                                      # a yes works once
        self._log("confirmed", **self._what(req))
        if req["risk"] == "dangerous":
            req["state"], req["laptop_since"] = "waiting_laptop", self.clock()
            s = self.services
            s.approvals.request(APPROVAL_KIND, f"Your phone ({req['device_name']}) wants to: {req['summary']}",
                                {"request": req["id"], "action": req["action"]})
            try:
                s.notify("waiting", f"Your phone wants to control this laptop: {req['summary'][:80]}. Approve it first.")
            except Exception:                                          # noqa: BLE001
                pass
            return self._view(req)
        return self._execute(req)

    def _execute(self, req: dict) -> dict:
        action = ACTIONS[req["action"]]
        req["state"] = "running"
        try:
            req["result"] = str(action.run(self.services, req["params"], req["payload"]))[:2000]
            req["state"] = "done"
            self._log("ran", **self._what(req))
        except RemoteError as e:
            req["state"], req["error"] = "failed", e.message
            self._log("failed", **self._what(req))
        except Exception as e:                                         # noqa: BLE001
            # the laptop and PC-control errors are written in plain words; anything else stays generic
            from prometheus.laptop import LaptopError
            from prometheus.pc_control.errors import PCControlError

            known = isinstance(e, (LaptopError, PCControlError))
            req["state"], req["error"] = "failed", str(e)[:300] if known else "That didn't work on the laptop."
            self._log("failed", **self._what(req))
        return self._view(req)

    def cancel(self, device: dict, request_id: str) -> dict:
        with self._lock:
            req = self._get(device, request_id)
            if req["state"] in ("waiting_phone", "waiting_laptop"):
                req["state"], req["confirm"] = "cancelled", None
                self._log("cancelled", **self._what(req))
            return self._view(req)

    def status(self, device: dict, request_id: str) -> dict:
        with self._lock:
            return self._view(self._get(device, request_id))

    def run_approved(self, payload: dict) -> str:
        """The owner's yes on the laptop (the Approvals handler). Never raises: it says in words why nothing ran."""
        with self._lock:
            req = self._requests.get(str(payload.get("request", "")))
            if req is None:
                return "Prometheus restarted since your phone asked, so nothing was done. Ask again from the phone."
            if req["state"] == "waiting_laptop" and self.clock() > req["laptop_since"] + LAPTOP_TTL:
                req["state"] = "expired"
            if req["state"] != "waiting_laptop":
                return f"That request is {req['state'].replace('_', ' ')}, so nothing was done. Ask again from the phone."
            if self.stopped():
                return "Everything is stopped, so nothing was done."
            self._log("laptop_yes", **self._what(req))
            out = self._execute(req)
        return out["result"] or out["error"] or "Done."
