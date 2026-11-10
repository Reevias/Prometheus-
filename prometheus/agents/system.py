"""Laptop control: a fixed list of actions (Wi-Fi, Bluetooth, location, battery). No free command line."""
from __future__ import annotations

from pathlib import Path

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.laptop import Laptop, LaptopError
from prometheus.safety.approvals import TryAgain

SUBMIT_KEYS = {"enter", "ctrl+enter"}
ASK_POWER = {"sleep", "shutdown", "restart", "logoff"}
ACTIONS = {"wifi_status", "wifi_list", "wifi_connect", "wifi_disconnect", "bluetooth_status", "bluetooth_on",
           "bluetooth_off", "location", "battery", "apps_running", "app_open", "app_close", "window", "volume",
           "mute", "brightness", "dark_mode", "screenshot", "type_text", "press_keys", "click", "power",
           "install_app"}
READ = {"wifi_status", "wifi_list", "bluetooth_status", "location", "battery", "apps_running"}
CHANGE = ACTIONS - READ


def needs_approval(action: str, params: dict) -> str:
    """Why this action must wait for the owner's tap ('' = it may run right away)."""
    if action == "press_keys" and str(params.get("keys", "")).strip().lower() in SUBMIT_KEYS:
        return "Pressing Enter can send or submit something."
    if action == "click":
        return "A click could press Send or Buy; Prometheus can't tell what is under the pointer."
    if action == "power" and str(params.get("mode", "")).lower() in ASK_POWER:
        return "This changes the power state of the laptop."
    if action == "install_app":
        return "Installing software."
    return ""


def perform(action: str, params: dict, lp: Laptop) -> str:
    """Run one laptop action. Raises LaptopError. Used both directly and after the owner approves."""
    t = params.get("target")
    if action == "app_open":
        return lp.app_open(t)
    if action == "app_close":
        return lp.app_close(t)
    if action == "window":
        return lp.window(t, str(params.get("mode", "focus")))
    if action == "volume":
        return lp.volume(params.get("level"))
    if action == "mute":
        return lp.mute()
    if action == "brightness":
        return lp.brightness(params.get("level"))
    if action == "dark_mode":
        return lp.dark_mode(str(params.get("mode", "")))
    if action == "screenshot":
        return f"Saved a screenshot: {lp.screenshot()}"
    if action == "type_text":
        return lp.type_text(params.get("text"))
    if action == "press_keys":
        return lp.press_keys(params.get("keys", ""))
    if action == "click":
        return lp.click(params.get("x"), params.get("y"))
    if action == "power":
        return lp.power(str(params.get("mode", "")))
    if action == "install_app":
        return lp.install_app(t)
    if action == "wifi_connect_open":
        return lp.wifi_connect_new(str(t or ""), None)
    raise LaptopError(f"'{action}' is not something I can do on the laptop.")


def laptop_action_handler(payload: dict, lp: Laptop) -> str:
    try:
        return perform(str(payload.get("action", "")), payload, lp)
    except LaptopError as e:
        raise RuntimeError(str(e)) from e


def wifi_password_handler(payload: dict, secret: str, laptop: Laptop, workdir: Path | None = None) -> str:
    """Runs when the owner types the Wi-Fi password into the app. The password goes straight to Windows."""
    try:
        return laptop.wifi_connect_new(str(payload.get("ssid", "")), secret, workdir=workdir)
    except LaptopError as e:
        if "password" in str(e).lower():
            raise TryAgain(str(e)) from e      # a typo: keep the approval so it can be tried again
        raise RuntimeError(str(e)) from e


@register
class SystemAgent(Agent):
    name = "system"
    description = ("Controls this laptop with a fixed list of actions. Inputs: action, one of wifi_status, wifi_list, "
                   "wifi_connect (ssid), wifi_disconnect, bluetooth_status, bluetooth_on, bluetooth_off, location, "
                   "battery, apps_running, app_open (target = app name), app_close (target), window (target, mode = "
                   "minimize|maximize|restore|focus), volume (level 0-100), mute, brightness (level 0-100), dark_mode "
                   "(mode = on|off), screenshot, type_text (text, one line), press_keys (keys, e.g. ctrl+c, enter), "
                   "click (x, y), power (mode = lock|sleep|shutdown|restart|logoff), install_app (target = winget "
                   "id). Pressing Enter, clicking, power changes and installs wait for the owner's approval. "
                   "If a Wi-Fi password is needed the app asks the owner for it; never put a password in the inputs.")
    tools = {"system_read", "system_change"}
    required_inputs = ("action",)
    private_outputs = True     # network names and location are personal: answers about them stay local
    accepts_private = False

    def __init__(self, laptop: Laptop | None = None):
        self.laptop = laptop or Laptop()

    def _wifi_connect(self, ctx: AgentContext, ssid: str) -> StepResult:
        lp = self.laptop
        if ssid in lp.wifi_known():
            return StepResult("done", lp.wifi_connect_known(ssid))
        net = next((n for n in lp.wifi_networks() if n["ssid"] == ssid), None)
        if net is None:
            seen = ", ".join(n["ssid"] for n in lp.wifi_networks()[:8]) or "none"
            return StepResult("failed", f"I can't see a Wi-Fi network called '{ssid}'. In range: {seen}.")
        if net["open"]:
            ctx.approvals.request("laptop_action", f"Join the open Wi-Fi network '{ssid}' (anyone nearby can pose as an "
                                                   "open network, and it is not encrypted)",
                                  {"action": "wifi_connect_open", "target": ssid})
            return StepResult("waiting_approval", f"'{ssid}' is an open network. Approve joining it on the Approvals page.")
        ctx.approvals.request("wifi_password", f"Wi-Fi password needed for '{ssid}'", {"ssid": ssid},
                              secret_label="Wi-Fi password")
        return StepResult("waiting_approval",
                          f"'{ssid}' needs a password I don't have. Type it on the Approvals page and I'll connect.")

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        action = str(inputs.get("action") or "").strip().lower()
        if action not in ACTIONS:
            return StepResult("failed", f"'{action}' is not something I can do on the laptop. I can do: "
                                        + ", ".join(sorted(ACTIONS)) + ".")
        ctx.use(self.name, "system_change" if action in CHANGE else "system_read")
        lp = self.laptop
        try:
            if action == "wifi_status":
                s = lp.wifi_status()
                text = f"Connected to {s['ssid']} (signal {s['signal']})." if s["connected"] else "Not connected to Wi-Fi."
            elif action == "wifi_list":
                nets = lp.wifi_networks()
                text = "Wi-Fi in range:\n" + "\n".join(
                    f"- {n['ssid']} ({n['signal']}{', open' if n['open'] else ''})" for n in nets) if nets else "No Wi-Fi in range."
            elif action == "wifi_connect":
                result = self._wifi_connect(ctx, str(inputs.get("ssid") or "").strip())
                ctx.store.audit(self.name, action, {"ssid": str(inputs.get("ssid") or "")[:32], "status": result.status})
                return result
            elif action in ACTIONS - {"wifi_status", "wifi_list", "wifi_connect", "wifi_disconnect", "bluetooth_status",
                                      "bluetooth_on", "bluetooth_off", "location", "battery", "apps_running"}:
                why = needs_approval(action, inputs)
                if why:
                    shown = {k: v for k, v in inputs.items() if k not in ("action",) and k in
                             ("target", "mode", "level", "text", "keys", "x", "y")}
                    ctx.approvals.request("laptop_action", f"{action.replace('_', ' ')}: {why}",
                                          {"action": action, **{k: str(v)[:200] for k, v in shown.items()}})
                    return StepResult("waiting_approval", f"{why} Approve it on the Approvals page and I'll do it.")
                text = perform(action, inputs, lp)
                ctx.store.audit(self.name, action, {k: str(v)[:60] for k, v in inputs.items() if k != "text"})
                return StepResult("done", text)
            elif action == "apps_running":
                apps = lp.apps_running()
                text = "Open windows:\n" + "\n".join(f"- {a['name']}: {a['title']}" for a in apps) if apps else "No open windows."
            elif action == "wifi_disconnect":
                text = lp.wifi_disconnect()
            elif action.startswith("bluetooth"):
                want = {"bluetooth_status": "status", "bluetooth_on": "On", "bluetooth_off": "Off"}[action]
                text = f"Bluetooth is {lp.bluetooth(want).lower()}."
            elif action == "location":
                loc = lp.location()
                text = (f"Your laptop is near latitude {loc['latitude']:.4f}, longitude {loc['longitude']:.4f} "
                        f"(within about {loc['accuracy_m']:.0f} m).")
            else:
                b = lp.battery()
                text = f"Battery is at {b['percent']}%{', charging' if b['charging'] else ''}."
        except LaptopError as e:
            return StepResult("failed", str(e))
        if action in CHANGE:
            ctx.store.audit(self.name, action, {})
        return StepResult("done", text)
