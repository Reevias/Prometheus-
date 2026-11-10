"""Laptop control: a fixed list of actions (Wi-Fi, Bluetooth, location, battery). No free command line."""
from __future__ import annotations

from pathlib import Path

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.laptop import Laptop, LaptopError

READ = {"wifi_status", "wifi_list", "bluetooth_status", "location", "battery"}
CHANGE = {"wifi_connect", "wifi_disconnect", "bluetooth_on", "bluetooth_off"}


def wifi_password_handler(payload: dict, secret: str, laptop: Laptop, workdir: Path | None = None) -> str:
    """Runs when the owner types the Wi-Fi password into the app. The password goes straight to Windows."""
    try:
        return laptop.wifi_connect_new(str(payload.get("ssid", "")), secret, workdir=workdir)
    except LaptopError as e:
        raise RuntimeError(str(e)) from e


@register
class SystemAgent(Agent):
    name = "system"
    description = ("Controls this laptop with a fixed list of actions. Inputs: action, one of wifi_status, wifi_list, "
                   "wifi_connect (needs ssid = the network name), wifi_disconnect, bluetooth_status, bluetooth_on, "
                   "bluetooth_off, location, battery. If a Wi-Fi password is needed the app asks the owner for it; "
                   "never put a password in the inputs.")
    tools = {"system_read", "system_change"}
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
            return StepResult("done", lp.wifi_connect_new(ssid, None))
        ctx.approvals.request("wifi_password", f"Wi-Fi password needed for '{ssid}'", {"ssid": ssid},
                              secret_label="Wi-Fi password")
        return StepResult("waiting_approval",
                          f"'{ssid}' needs a password I don't have. Type it on the Approvals page and I'll connect.")

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        action = str(inputs.get("action") or "").strip().lower()
        if action not in READ | CHANGE:
            return StepResult("failed", f"'{action}' is not something I can do on the laptop. I can do: "
                                        + ", ".join(sorted(READ | CHANGE)) + ".")
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
