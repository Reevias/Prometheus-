"""Fixed laptop actions on Windows: Wi-Fi, Bluetooth, location, battery.

There is no free command line here. Every action runs one fixed command; the only outside values that ever reach
a command are a checked Wi-Fi name and a Wi-Fi password (written to a short-lived profile file, then deleted).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

SSID = re.compile(r'^[^\x00-\x1f"<>|^%]{1,32}$')
PASSWORD = re.compile(r"^[\x20-\x7e]{8,63}$")

PS = ["powershell", "-NoProfile", "-NonInteractive", "-Command"]
BLUETOOTH_SCRIPT = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
Function Await($task, $type) { $m = $asTaskGeneric.MakeGenericMethod($type); $t = $m.Invoke($null, @($task)); $t.Wait(-1) | Out-Null; $t.Result }
[Windows.Devices.Radios.Radio,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioAccessStatus,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Radios.RadioState,Windows.System.Devices,ContentType=WindowsRuntime] | Out-Null
Await ([Windows.Devices.Radios.Radio]::RequestAccessAsync()) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null
$radios = Await ([Windows.Devices.Radios.Radio]::GetRadiosAsync()) ([System.Collections.Generic.IReadOnlyList[Windows.Devices.Radios.Radio]])
$bt = $radios | Where-Object { $_.Kind -eq 'Bluetooth' } | Select-Object -First 1
if (-not $bt) { 'NONE'; exit }
$want = '__WANT__'
if ($want -ne 'status') { Await ($bt.SetStateAsync($want)) ([Windows.Devices.Radios.RadioAccessStatus]) | Out-Null }
$bt.State
"""
LOCATION_SCRIPT = r"""
Add-Type -AssemblyName System.Device
$w = New-Object System.Device.Location.GeoCoordinateWatcher
$w.Start(); $n = 0
while (($w.Status -ne 'Ready') -and ($w.Permission -ne 'Denied') -and ($n -lt 50)) { Start-Sleep -Milliseconds 200; $n++ }
if ($w.Permission -eq 'Denied') { 'DENIED' } elseif ($w.Status -ne 'Ready') { 'UNKNOWN' } else {
  $c = $w.Position.Location; $i = [cultureinfo]::InvariantCulture
  '{0},{1},{2}' -f $c.Latitude.ToString($i), $c.Longitude.ToString($i), $c.HorizontalAccuracy.ToString($i) }
"""
BATTERY_SCRIPT = ("Get-CimInstance Win32_Battery | Select-Object -First 1 EstimatedChargeRemaining,BatteryStatus "
                  "| ConvertTo-Json -Compress")
PROFILE_XML = """<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
  <name>{name}</name>
  <SSIDConfig><SSID><name>{name}</name></SSID></SSIDConfig>
  <connectionType>ESS</connectionType>
  <connectionMode>auto</connectionMode>
  <MSM><security>{security}</security></MSM>
</WLANProfile>
"""
SECURED = ("<authEncryption><authentication>WPA2PSK</authentication><encryption>AES</encryption>"
           "<useOneX>false</useOneX></authEncryption>"
           "<sharedKey><keyType>passPhrase</keyType><protected>false</protected><keyMaterial>{key}</keyMaterial></sharedKey>")
OPEN = ("<authEncryption><authentication>open</authentication><encryption>none</encryption>"
        "<useOneX>false</useOneX></authEncryption>")


class LaptopError(Exception):
    pass


def _run(argv: list[str], timeout: int = 20) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise LaptopError(f"Windows did not answer: {type(e).__name__}") from e
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _fields(text: str) -> list[tuple[str, str]]:
    out = []
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            out.append((key.strip(), value.strip()))
    return out


class Laptop:
    def __init__(self, run: Callable[..., tuple[int, str]] | None = None, platform: str = sys.platform):
        self.run = run or _run
        self.platform = platform

    def _need_windows(self) -> None:
        if self.platform != "win32":
            raise LaptopError("Laptop controls only work on Windows.")

    def _check_ssid(self, ssid: str) -> str:
        if not isinstance(ssid, str) or not SSID.match(ssid):
            raise LaptopError("That Wi-Fi name is not valid.")
        return ssid

    # -- Wi-Fi ---------------------------------------------------------------
    def wifi_status(self) -> dict:
        self._need_windows()
        fields = dict(reversed(_fields(self.run(["netsh", "wlan", "show", "interfaces"])[1])))
        state = fields.get("State", "").lower()
        return {"connected": state == "connected", "ssid": fields.get("SSID", "") if state == "connected" else "",
                "signal": fields.get("Signal", "")}

    def wifi_networks(self) -> list[dict]:
        self._need_windows()
        nets: list[dict] = []
        for key, value in _fields(self.run(["netsh", "wlan", "show", "networks", "mode=bssid"])[1]):
            if re.fullmatch(r"SSID \d+", key):
                nets.append({"ssid": value, "open": False, "signal": ""})
            elif nets and key == "Authentication":
                nets[-1]["open"] = value.lower() == "open"
            elif nets and key == "Signal" and not nets[-1]["signal"]:
                nets[-1]["signal"] = value
        return [n for n in nets if n["ssid"]]

    def wifi_known(self) -> list[str]:
        self._need_windows()
        return [v for k, v in _fields(self.run(["netsh", "wlan", "show", "profiles"])[1]) if k.endswith("Profile") and v]

    def wifi_connect_known(self, ssid: str) -> str:
        self._need_windows()
        ssid = self._check_ssid(ssid)
        code, out = self.run(["netsh", "wlan", "connect", f"name={ssid}", f"ssid={ssid}"])
        if code != 0:
            raise LaptopError(f"Windows could not connect to {ssid}: {out.strip()[:200]}")
        return f"Asked Windows to connect to {ssid}."

    def wifi_connect_new(self, ssid: str, password: str | None, workdir: Path | None = None) -> str:
        """Save a new network (with its password, or open if None) and connect. The profile file is always removed."""
        self._need_windows()
        ssid = self._check_ssid(ssid)
        if password is not None and not PASSWORD.match(password):
            raise LaptopError("A Wi-Fi password has 8 to 63 normal characters.")
        security = OPEN if password is None else SECURED.format(key=escape(password))
        xml = PROFILE_XML.format(name=escape(ssid), security=security)
        folder = Path(workdir) if workdir else Path(tempfile.gettempdir())
        folder.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix="prometheus-wifi-", suffix=".xml", dir=folder)
        path = Path(name)
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(xml)
            code, out = self.run(["netsh", "wlan", "add", "profile", f"filename={path}", "user=current"])
            if code != 0:
                raise LaptopError(f"Windows refused the network settings: {out.strip()[:200]}")
        finally:
            path.unlink(missing_ok=True)
        return self.wifi_connect_known(ssid)

    def wifi_disconnect(self) -> str:
        self._need_windows()
        self.run(["netsh", "wlan", "disconnect"])
        return "Disconnected from Wi-Fi."

    # -- Bluetooth, location, battery ------------------------------------------
    def bluetooth(self, want: str) -> str:
        self._need_windows()
        if want not in ("status", "On", "Off"):
            raise LaptopError("Bluetooth can only be turned on, turned off, or checked.")
        out = self.run([*PS, BLUETOOTH_SCRIPT.replace("__WANT__", want)], timeout=30)[1].strip().splitlines()
        state = out[-1].strip() if out else ""
        if state == "NONE":
            raise LaptopError("This laptop has no Bluetooth radio.")
        if state not in ("On", "Off", "Disabled", "Unknown"):
            raise LaptopError("Windows did not report the Bluetooth state.")
        return state

    def location(self) -> dict:
        self._need_windows()
        out = self.run([*PS, LOCATION_SCRIPT], timeout=30)[1].strip().splitlines()
        last = out[-1].strip() if out else "UNKNOWN"
        if last == "DENIED":
            raise LaptopError("Location is switched off in Windows (Settings, Privacy and security, Location).")
        try:
            lat, lon, acc = (float(x) for x in last.split(","))
        except ValueError as e:
            raise LaptopError("Windows could not work out the location.") from e
        return {"latitude": lat, "longitude": lon, "accuracy_m": acc}

    def battery(self) -> dict:
        self._need_windows()
        try:
            data = json.loads(self.run([*PS, BATTERY_SCRIPT])[1].strip() or "{}")
        except ValueError as e:
            raise LaptopError("Windows did not report the battery.") from e
        if not data:
            raise LaptopError("No battery found (is this a desktop?).")
        return {"percent": int(data.get("EstimatedChargeRemaining") or 0), "charging": data.get("BatteryStatus") == 2}
