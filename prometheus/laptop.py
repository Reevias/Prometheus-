"""Fixed laptop actions on Windows: Wi-Fi, Bluetooth, location, battery.

There is no free command line here. Every action runs one fixed command; the only outside values that ever reach
a command are a checked Wi-Fi name and a Wi-Fi password (written to a short-lived profile file, then deleted).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable
from xml.sax.saxutils import escape

SSID = re.compile(r'[^\x00-\x1f\x7f-\x9f\u2028\u2029"<>|^%]{1,32}')
PASSWORD = re.compile(r"[\x20-\x7e]{8,63}")
SYSTEM32 = {"netsh": "netsh.exe", "shutdown": "shutdown.exe", "rundll32.exe": "rundll32.exe",
            "powershell": r"WindowsPowerShell\v1.0\powershell.exe"}

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
  <connectionMode>{mode}</connectionMode>
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


class AppNotInstalled(LaptopError):
    """No app by that name is installed (so a website or the Microsoft Store may help)."""


def _wipe(path: Path) -> None:
    """Overwrite then delete a file that held a password. If Windows won't let go of it, at least it is blank."""
    for attempt in range(5):
        try:
            if path.exists():
                path.write_bytes(b"\0" * max(path.stat().st_size, 1))
            path.unlink(missing_ok=True)
            return
        except OSError:
            time.sleep(0.2)


def sweep_wifi_profiles(folder: Path | None = None) -> None:
    """Remove password files left behind by a crash."""
    folder = Path(folder) if folder else Path(tempfile.gettempdir())
    try:
        for f in folder.glob("prometheus-wifi-*.xml"):
            _wipe(f)
    except OSError:
        pass


def _exe(name: str) -> str:
    """On Windows use the real program in System32, never whatever happens to be in the current folder."""
    if sys.platform == "win32" and name in SYSTEM32:
        full = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", SYSTEM32[name])
        if os.path.isfile(full):
            return full
    return name


def _run(argv: list[str], timeout: int = 20, env: dict | None = None) -> tuple[int, str]:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    full_env = {**os.environ, **env} if env else None
    argv = [_exe(argv[0]), *argv[1:]]
    try:
        proc = subprocess.run(argv, capture_output=True, timeout=timeout, creationflags=flags, env=full_env,
                              encoding="oem" if sys.platform == "win32" else "utf-8", errors="replace")
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
        try:
            ok = isinstance(ssid, str) and SSID.fullmatch(ssid) and len(ssid.encode("utf-8")) <= 32
        except UnicodeEncodeError:
            ok = False
        if not ok:
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
        if password is not None and not (isinstance(password, str) and PASSWORD.fullmatch(password)):
            raise LaptopError("A Wi-Fi password has 8 to 63 normal characters.")
        security = OPEN if password is None else SECURED.format(key=escape(password))
        # An open network is joined only this once, never automatically later (guards against look-alike networks).
        xml = PROFILE_XML.format(name=escape(ssid), security=security,
                                 mode="manual" if password is None else "auto")
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
            _wipe(path)
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


# ======================= apps, windows, input and settings =======================
# Anything you ask for (an app name, text to type, a level) is passed in an environment variable and read by a
# fixed script. It is never pasted into command text.
APP = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._()+-]{0,59}$")
PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,79}$")
TERMINALS = {"powershell", "powershell_ise", "pwsh", "cmd", "conhost", "windowsterminal", "wt", "wsl", "wslhost",
             "bash", "mintty", "alacritty", "regedit", "mmc", "taskmgr", "openconsole",
             # programming prompts: typed text would run as code
             "python", "pythonw", "py", "ipython", "node", "ruby", "perl", "ubuntu", "debian", "kali", "git-bash"}
# words that mark a terminal or admin tool anywhere in an app's name ("Windows Power" matching PowerShell)
NEVER_IN_NAME = ("terminal", "powershell", "command prompt", "registry", "python", "bash", "ubuntu", "debian", "kali",
                 "wsl", "console", "cmd.exe", "node.js")
NEVER_OPEN = TERMINALS | {"windows terminal", "windows powershell", "command prompt", "terminal", "registry editor",
                          "task manager", "microsoft management console", "services",
                          # script runners and admin tools: harmless-looking names that can run or change anything
                          "wscript", "cscript", "mshta", "rundll32", "regsvr32", "msiexec", "certutil", "bitsadmin",
                          "schtasks", "sc", "netsh", "reg", "diskpart", "format", "bcdedit", "vssadmin", "wmic",
                          "cipher", "takeown", "icacls", "net", "net1", "runas", "control", "msconfig", "gpedit",
                          "secpol", "compmgmt", "devmgmt", "diskmgmt", "eventvwr", "perfmon", "powercfg", "sfc", "dism"}
KEYS = {"enter": "{ENTER}", "ctrl+enter": "^{ENTER}", "tab": "{TAB}", "shift+tab": "+{TAB}", "esc": "{ESC}",
        "up": "{UP}", "down": "{DOWN}", "left": "{LEFT}", "right": "{RIGHT}", "backspace": "{BACKSPACE}",
        "delete": "{DELETE}", "home": "{HOME}", "end": "{END}", "pageup": "{PGUP}", "pagedown": "{PGDN}",
        "ctrl+a": "^a", "ctrl+c": "^c", "ctrl+v": "^v", "ctrl+x": "^x", "ctrl+z": "^z", "ctrl+y": "^y",
        "ctrl+s": "^s", "ctrl+f": "^f", "ctrl+t": "^t", "ctrl+w": "^w", "ctrl+l": "^l", "ctrl+n": "^n",
        "ctrl+p": "^p", "ctrl+tab": "^{TAB}", "alt+tab": "%{TAB}", "f5": "{F5}", "f11": "{F11}"}
SUBMIT_KEYS = {"enter", "ctrl+enter"}
POWER = {"lock": ["rundll32.exe", "user32.dll,LockWorkStation"],
         "sleep": ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
         "shutdown": ["shutdown", "/s", "/t", "30"], "restart": ["shutdown", "/r", "/t", "30"],
         "logoff": ["shutdown", "/l"]}

WIN32 = r"""
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class W {
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr e);
}
"@
"""
FOREGROUND_SCRIPT = WIN32 + r"""
$h = [W]::GetForegroundWindow(); $p = [uint32]0; [W]::GetWindowThreadProcessId($h, [ref]$p) | Out-Null
'ForegroundProcess:' + (Get-Process -Id $p -ErrorAction SilentlyContinue).ProcessName
"""
LIST_SCRIPT = ("Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object -First 40 "
               "@{n='Name';e={$_.ProcessName}}, MainWindowTitle | ConvertTo-Json -Compress")
OPEN_SCRIPT = r"""
$n = $env:PROM_APP
$exact = $env:PROM_NEVER -split '\|'; $words = $env:PROM_NEVER_IN -split '\|'
function Blocked($name) {
  $l = ([string]$name).ToLower(); $b = [IO.Path]::GetFileNameWithoutExtension($l)
  if ($exact -contains $l -or $exact -contains $b) { return $true }
  foreach ($w in $words) { if ($w -and $l.Contains($w)) { return $true } }
  return $false
}
$app = Get-StartApps | Where-Object { $_.Name -eq $n } | Select-Object -First 1
if (-not $app) { $app = Get-StartApps | Where-Object { $_.Name -like "*$n*" } | Select-Object -First 1 }
if ($app -and ((Blocked $app.Name) -or (Blocked $app.AppID))) { 'blocked ' + $app.Name }
elseif ($app) { Start-Process ('shell:AppsFolder\' + $app.AppID); 'opened ' + $app.Name }
else {
  $c = Get-Command $n -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $c) { $c = Get-Command ($n + '.exe') -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1 }
  if ($c -and (Blocked $c.Name)) { 'blocked ' + $c.Name }
  elseif ($c) { Start-Process -FilePath $c.Source; 'opened ' + $c.Name } else { 'notfound' }
}
"""
STORE_SCRIPT = r"""
$q = $env:PROM_TEXT
if ($q) { Start-Process ('ms-windows-store://search/?query=' + [uri]::EscapeDataString($q)) }
else { Start-Process 'ms-windows-store://home' }
'opened'
"""
# Finding an app to install by its name. The PowerShell module gives clean data; the text table is the fallback.
FIND_SCRIPT = r"""
$q = $env:PROM_TEXT
try {
  Import-Module Microsoft.WinGet.Client -ErrorAction Stop
  Find-WinGetPackage -Query $q -ErrorAction Stop | Select-Object -First 8 Name, Id, Source | ConvertTo-Json -Compress
} catch { 'TABLE'; winget search --query $q --accept-source-agreements --disable-interactivity }
"""
CLOSE_SCRIPT = r"""
$n = $env:PROM_APP; $c = 0
Get-Process | Where-Object { $_.MainWindowTitle -and ($_.ProcessName -eq $n -or $_.MainWindowTitle -like "*$n*") -and
  $_.ProcessName -notmatch '^(python|pythonw|py|uvicorn|prometheus)' -and $_.MainWindowTitle -notlike '*Prometheus*' } |
  ForEach-Object { if ($_.CloseMainWindow()) { $c++ } }
$c
"""
WINDOW_SCRIPT = WIN32 + r"""
$n = $env:PROM_APP; $m = $env:PROM_MODE; $c = 0
$cmd = @{ minimize = 6; maximize = 3; restore = 9; focus = 9 }[$m]
Get-Process | Where-Object { $_.MainWindowHandle -ne 0 -and ($_.ProcessName -eq $n -or $_.MainWindowTitle -like "*$n*") } |
  Select-Object -First 1 | ForEach-Object { [W]::ShowWindow($_.MainWindowHandle, $cmd) | Out-Null;
  if ($m -eq 'focus' -or $m -eq 'restore') { [W]::SetForegroundWindow($_.MainWindowHandle) | Out-Null }; $c++ }
$c
"""
VOLUME_SCRIPT = r"""
$w = New-Object -ComObject WScript.Shell; $l = [int]$env:PROM_LEVEL
1..50 | ForEach-Object { $w.SendKeys([char]174) }
if ($l -gt 0) { 1..([math]::Floor($l / 2)) | ForEach-Object { $w.SendKeys([char]175) } }
'ok'
"""
MUTE_SCRIPT = "(New-Object -ComObject WScript.Shell).SendKeys([char]173); 'ok'"
BRIGHTNESS_SCRIPT = ("(Get-WmiObject -Namespace root/WMI -Class WmiMonitorBrightnessMethods)"
                     ".WmiSetBrightness(1, [int]$env:PROM_LEVEL); 'ok'")
DARK_SCRIPT = (r"$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize'; "
               "Set-ItemProperty -Path $k -Name AppsUseLightTheme -Value ([int]$env:PROM_MODE); "
               "Set-ItemProperty -Path $k -Name SystemUsesLightTheme -Value ([int]$env:PROM_MODE); 'ok'")
SHOT_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
$b = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bmp = New-Object System.Drawing.Bitmap $b.Width, $b.Height
$g = [System.Drawing.Graphics]::FromImage($bmp); $g.CopyFromScreen($b.Left, $b.Top, 0, 0, $bmp.Size)
New-Item -ItemType Directory -Force -Path $env:PROM_DIR | Out-Null
$f = Join-Path $env:PROM_DIR ('screenshot-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.png')
$bmp.Save($f, [System.Drawing.Imaging.ImageFormat]::Png); $f
"""
TYPE_SCRIPT = ("Add-Type -AssemblyName System.Windows.Forms; "
               "[System.Windows.Forms.SendKeys]::SendWait($env:PROM_TEXT); 'ok'")
KEYS_SCRIPT = ("Add-Type -AssemblyName System.Windows.Forms; "
               "[System.Windows.Forms.SendKeys]::SendWait($env:PROM_KEYS); 'ok'")
CLICK_SCRIPT = WIN32 + r"""
[W]::SetCursorPos([int]$env:PROM_X, [int]$env:PROM_Y) | Out-Null
[W]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero); [W]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero); 'ok'
"""


def sendkeys_escape(text: str) -> str:
    """Text to type, made safe for SendKeys. One line only: a line break would press Enter."""
    if not isinstance(text, str) or not text or len(text) > 500:
        raise LaptopError("Type between 1 and 500 characters.")
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise LaptopError("Line breaks and control characters are not typed. Press Enter as a separate step.")
    return "".join("{" + c + "}" if c in "+^%~(){}[]" else c for c in text)


def _level(value) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip().isdigit():
        raise LaptopError("A level is a whole number from 0 to 100.")
    n = int(str(value).strip())
    if not 0 <= n <= 100:
        raise LaptopError("A level is a whole number from 0 to 100.")
    return n


def _app_name(name) -> str:
    if not isinstance(name, str) or not APP.fullmatch(name.strip()):
        raise LaptopError("That app name is not valid.")
    return name.strip()


def _blocked(name: str) -> bool:
    low = name.lower()
    return (low in NEVER_OPEN or low.removesuffix(".exe") in NEVER_OPEN
            or any(w in low for w in NEVER_IN_NAME))


def _ps(self, script: str, env: dict | None = None, timeout: int = 30) -> str:
    return self.run([*PS, script], timeout=timeout, env=env or {})[1].strip()


def _foreground_ok(self) -> None:
    """Refuse to type or click unless we positively know the window in front is not a terminal."""
    out = _ps(self, FOREGROUND_SCRIPT)
    marker = "ForegroundProcess:"
    if marker not in out:
        raise LaptopError("I could not tell which window is in front, so I did not type or click.")
    name = out.rsplit(marker, 1)[-1].strip().lower()
    if not name:
        raise LaptopError("I could not tell which window is in front, so I did not type or click.")
    if name in TERMINALS:
        raise LaptopError("The window in front is a terminal or admin tool. Prometheus never types into those.")


def app_open(self, name: str) -> str:
    self._need_windows()
    name = _app_name(name)
    if _blocked(name):
        raise LaptopError(f"Prometheus never opens {name}: terminals and admin tools are off limits.")
    out = _ps(self, OPEN_SCRIPT, {"PROM_APP": name, "PROM_NEVER": "|".join(sorted(NEVER_OPEN)),
                                  "PROM_NEVER_IN": "|".join(NEVER_IN_NAME)})
    last = out.splitlines()[-1].strip() if out.strip() else ""
    if last.startswith("blocked "):
        raise LaptopError(f"Prometheus never opens {last[8:]}: terminals and admin tools are off limits.")
    if last == "notfound":
        raise AppNotInstalled(f"{name} isn't installed on this laptop.")
    return last or f"Asked Windows to open {name}."


NOTIFY_SCRIPT = r"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$x = $t.GetElementsByTagName('text')
$x.Item(0).AppendChild($t.CreateTextNode('Prometheus')) > $null
$x.Item(1).AppendChild($t.CreateTextNode($env:PROM_TEXT)) > $null
$app = 'Reevias.Prometheus'
$key = 'HKCU:\Software\Classes\AppUserModelId\' + $app
if (-not (Test-Path $key)) { New-Item -Path $key -Force > $null }
Set-ItemProperty -Path $key -Name DisplayName -Value 'Prometheus'
if ($env:PROM_ICON) { Set-ItemProperty -Path $key -Name IconUri -Value $env:PROM_ICON }
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""


def notify(self, text: str) -> None:
    """A Windows notification (with its sound). The text travels in an environment variable."""
    self._need_windows()
    icon = Path(__file__).resolve().parent / "web" / "static" / "icon.png"
    _ps(self, NOTIFY_SCRIPT, {"PROM_TEXT": str(text)[:300], "PROM_ICON": str(icon)})


CPU_SCRIPT = "(Get-CimInstance Win32_Processor | Measure-Object -Property LoadPercentage -Average).Average"


def cpu_load(self) -> int:
    """Average CPU use right now, 0 to 100."""
    self._need_windows()
    out = _ps(self, CPU_SCRIPT, timeout=20).strip().splitlines()
    try:
        return int(float(out[-1]))
    except (IndexError, ValueError) as e:
        raise LaptopError("Windows didn't report the CPU load.") from e


def store_open(self, query: str = "") -> str:
    """Open the Microsoft Store, on a search for `query` if given."""
    self._need_windows()
    query = (query or "").strip()
    if query and not APP.fullmatch(query):
        raise LaptopError("That is not a valid app name to search for.")
    _ps(self, STORE_SCRIPT, {"PROM_TEXT": query})
    return f"Opened the Microsoft Store{' on ' + query if query else ''}."


def parse_winget_table(text: str) -> list[dict]:
    """Rows of `winget search` output (Name, Id, Version, Source), using the header's column positions."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    head = next((i for i, l in enumerate(lines) if re.search(r"\bId\b", l) and re.search(r"\bName\b", l)), None)
    if head is None:
        return []
    header = lines[head]
    cols = [(m.group(), m.start()) for m in re.finditer(r"\S+", header)]
    names = [c for c, _ in cols]
    rows = []
    for line in lines[head + 1:]:
        if set(line) <= set("-─ "):
            continue
        cells = {}
        for k, (col, start) in enumerate(cols):
            end = cols[k + 1][1] if k + 1 < len(cols) else len(line)
            cells[col] = line[start:end].strip()
        if cells.get("Id") and PACKAGE.fullmatch(cells["Id"]):
            rows.append({"name": cells.get("Name", ""), "id": cells["Id"], "source": cells.get("Source", "")})
    return rows if "Id" in names else []


def find_package(self, name: str) -> list[dict]:
    """Apps matching a name, best first: [{name, id, source}]. Store apps have ids like 9NCBCSZSJRSB."""
    self._need_windows()
    name = _app_name(name)
    out = _ps(self, FIND_SCRIPT, {"PROM_TEXT": name}, timeout=120)
    if out.lstrip().startswith("TABLE"):
        found = parse_winget_table(out.split("TABLE", 1)[1])
    else:
        try:
            data = json.loads(out.strip() or "[]")
        except ValueError:
            data = []
        data = [data] if isinstance(data, dict) else data
        found = [{"name": str(d.get("Name", "")), "id": str(d.get("Id", "")), "source": str(d.get("Source", ""))}
                 for d in data if isinstance(d, dict) and PACKAGE.fullmatch(str(d.get("Id", "")))]
    low = name.lower()
    found.sort(key=lambda r: (r["name"].lower() != low, low not in r["name"].lower(), r["source"] != "msstore"))
    return found


def app_close(self, name: str) -> str:
    self._need_windows()
    name = _app_name(name)
    if "prometheus" in name.lower():
        raise LaptopError("Prometheus does not close itself.")
    count = _ps(self, CLOSE_SCRIPT, {"PROM_APP": name}).splitlines()[-1:] or ["0"]
    n = int(count[0]) if count[0].isdigit() else 0
    return (f"Closed {name} (it may ask you to save first)." if n else f"No open window matched '{name}'.")


def window(self, name: str, mode: str) -> str:
    self._need_windows()
    name = _app_name(name)
    if mode not in ("minimize", "maximize", "restore", "focus"):
        raise LaptopError("A window can be minimized, maximized, restored or brought to the front.")
    out = _ps(self, WINDOW_SCRIPT, {"PROM_APP": name, "PROM_MODE": mode}).splitlines()[-1:] or ["0"]
    return f"Window '{name}': {mode}." if out[0] != "0" else f"No window matched '{name}'."


def apps_running(self) -> list[dict]:
    self._need_windows()
    try:
        data = json.loads(_ps(self, LIST_SCRIPT) or "[]")
    except ValueError as e:
        raise LaptopError("Windows did not list the open windows.") from e
    data = [data] if isinstance(data, dict) else data
    return [{"name": d.get("Name", ""), "title": d.get("MainWindowTitle", "")} for d in data]


def volume(self, level) -> str:
    self._need_windows()
    n = _level(level)
    _ps(self, VOLUME_SCRIPT, {"PROM_LEVEL": str(n)})
    return f"Volume set to about {n}%."


def mute(self) -> str:
    self._need_windows()
    _ps(self, MUTE_SCRIPT)
    return "Toggled mute."


def brightness(self, level) -> str:
    self._need_windows()
    n = _level(level)
    _ps(self, BRIGHTNESS_SCRIPT, {"PROM_LEVEL": str(n)})
    return f"Brightness set to {n}% (built-in screen)."


def dark_mode(self, mode: str) -> str:
    self._need_windows()
    if mode not in ("on", "off"):
        raise LaptopError("Dark mode can be on or off.")
    _ps(self, DARK_SCRIPT, {"PROM_MODE": "0" if mode == "on" else "1"})
    return f"Dark mode {mode}."


def screenshot(self) -> str:
    self._need_windows()
    folder = Path.home() / "Pictures" / "Prometheus"
    path = _ps(self, SHOT_SCRIPT, {"PROM_DIR": str(folder)}).splitlines()[-1:] or [""]
    if not path[0].lower().endswith(".png"):
        raise LaptopError("Windows did not save the screenshot.")
    return path[0]


def type_text(self, text: str) -> str:
    self._need_windows()
    safe = sendkeys_escape(text)
    _foreground_ok(self)
    _ps(self, TYPE_SCRIPT, {"PROM_TEXT": safe})
    return f"Typed {len(text)} characters."


def press_keys(self, keys: str) -> str:
    self._need_windows()
    key = str(keys).strip().lower()
    if key not in KEYS:
        raise LaptopError("That key combination is not on the allowed list.")
    _foreground_ok(self)
    _ps(self, KEYS_SCRIPT, {"PROM_KEYS": KEYS[key]})
    return f"Pressed {key}."


def click(self, x, y) -> str:
    self._need_windows()
    try:
        xi, yi = int(x), int(y)
    except (TypeError, ValueError) as e:
        raise LaptopError("A click needs x and y positions on the screen.") from e
    if not (0 <= xi <= 10000 and 0 <= yi <= 10000):
        raise LaptopError("That position is off the screen.")
    _foreground_ok(self)
    _ps(self, CLICK_SCRIPT, {"PROM_X": str(xi), "PROM_Y": str(yi)})
    return f"Clicked at {xi}, {yi}."


def power(self, mode: str) -> str:
    self._need_windows()
    if mode not in POWER:
        raise LaptopError("Power can be lock, sleep, shutdown, restart or logoff.")
    self.run(POWER[mode])
    return {"shutdown": "Shutting down in 30 seconds. Run `shutdown /a` to cancel.",
            "restart": "Restarting in 30 seconds. Run `shutdown /a` to cancel."}.get(mode, f"Done: {mode}.")


def install_app(self, package: str, source: str = "") -> str:
    self._need_windows()
    if not isinstance(package, str) or not PACKAGE.fullmatch(package):
        raise LaptopError("A winget package id looks like Publisher.Name.")
    if source not in ("", "winget", "msstore"):
        raise LaptopError("Apps install from winget or the Microsoft Store only.")
    argv = ["winget", "install", "--id", package, "-e", "--silent", "--accept-package-agreements",
            "--accept-source-agreements"] + (["--source", source] if source else [])
    code, out = self.run(argv, timeout=900)
    if code != 0:
        raise LaptopError(f"winget could not install {package}: {out.strip()[-200:]}")
    return f"Installed {package}."


DND_SCRIPT = r"""
$v = [int]$env:PROM_ON
$k = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings'
if (-not (Test-Path $k)) { New-Item -Path $k -Force > $null }
Set-ItemProperty -Path $k -Name 'NOC_GLOBAL_SETTING_TOASTS_ENABLED' -Value $v -Type DWord
$p = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications'
if (-not (Test-Path $p)) { New-Item -Path $p -Force > $null }
Set-ItemProperty -Path $p -Name 'ToastEnabled' -Value $v -Type DWord
"""


HEALTH_SCRIPT = r"""
$o = @{}
try { $o.startup = @(Get-CimInstance Win32_StartupCommand | Select-Object -ExpandProperty Name) } catch { $o.startup = @() }
try {
  $f = Join-Path $env:TEMP 'prom-battery.xml'
  powercfg /batteryreport /xml /output $f | Out-Null
  [xml]$x = Get-Content $f; Remove-Item $f -ErrorAction SilentlyContinue
  $b = $x.BatteryReport.Batteries.Battery
  $o.design = [int64]$b.DesignCapacity; $o.full = [int64]$b.FullChargeCapacity; $o.cycles = [int]$b.CycleCount
} catch { }
try {
  $s = (New-Object -ComObject Microsoft.Update.Session).CreateUpdateSearcher()
  $o.updates = $s.Search("IsInstalled=0 and IsHidden=0").Updates.Count
} catch { $o.updates = -1 }
$o.onedrive = [bool](Get-Process OneDrive -ErrorAction SilentlyContinue)
try { $o.fh = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\FileHistory' -ErrorAction Stop).ProtectedUpToTime } catch { }
$o | ConvertTo-Json -Compress
"""
SETTINGS_PAGES = {"startup": "ms-settings:startupapps", "updates": "ms-settings:windowsupdate",
                  "storage": "ms-settings:storagesense", "backup": "ms-settings:backup",
                  "battery": "ms-settings:batterysaver"}


def health_info(self) -> dict:
    """Read-only facts for the weekly health check: startup apps, battery wear, waiting updates, backups."""
    self._need_windows()
    out = _ps(self, HEALTH_SCRIPT, timeout=180)
    try:
        return json.loads(out.splitlines()[-1]) if out else {}
    except (ValueError, IndexError):
        return {}


def open_settings_page(self, page: str) -> str:
    """Open one of a few Windows Settings pages (a fixed list)."""
    self._need_windows()
    if page not in SETTINGS_PAGES:
        raise LaptopError("That Settings page isn't on the list.")
    os.startfile(SETTINGS_PAGES[page])          # an ms-settings: link from the fixed list above
    return f"Opened Windows Settings ({page})."


def do_not_disturb(self, on: bool) -> str:
    """Windows notification banners off (on=True) or back on. Prometheus's own notifications are held separately."""
    self._need_windows()
    _ps(self, DND_SCRIPT, {"PROM_ON": "0" if on else "1"})
    return "Do not disturb is on." if on else "Notifications are back on."


for _f in (app_open, app_close, window, apps_running, volume, mute, brightness, dark_mode, screenshot, type_text,
           press_keys, click, power, install_app, store_open, find_package, notify, cpu_load, do_not_disturb,
           health_info, open_settings_page):
    setattr(Laptop, _f.__name__, _f)
