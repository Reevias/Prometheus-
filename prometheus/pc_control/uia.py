"""Windows UI Automation reader via PowerShell UIAutomationClient (M6 6.4a)."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from prometheus.pc_control.errors import PCControlError, StoppedError

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PASSWORD_HINT_RE = re.compile(r"\b(password|passcode|passphrase|pin|secret|credential)\b", re.IGNORECASE)

_PROC_LOCK = threading.Lock()
_ACTIVE_PROCS: set[subprocess.Popen] = set()


@dataclass(frozen=True)
class ControlInfo:
    name: str
    control_type: str
    automation_id: str
    rect: tuple[int, int, int, int]
    focused: bool
    enabled: bool
    is_password: bool
    value: str = ""


def abort_active_ps() -> int:
    """Immediately kill any running UIA PowerShell subprocesses."""
    with _PROC_LOCK:
        procs = list(_ACTIVE_PROCS)
    killed = 0
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.kill()
                killed += 1
        except Exception:
            pass
    return killed


def run_ps_abortable(
    script: str,
    *,
    stop_check: Callable[[], bool] | None = None,
    timeout: float = 8.0,
) -> str:
    """Run Windows PowerShell with fast polling and instant termination on stop."""
    if sys.platform != "win32":
        raise PCControlError("Windows UI Automation requires Windows.")
    if stop_check and stop_check():
        raise StoppedError("Stopped before reading UI controls.")

    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=_CREATE_NO_WINDOW,
    )
    with _PROC_LOCK:
        _ACTIVE_PROCS.add(proc)
    try:
        deadline = time.monotonic() + timeout
        while proc.poll() is None:
            if stop_check and stop_check():
                proc.kill()
                proc.wait(timeout=0.5)
                raise StoppedError("Stopped while reading UI controls.")
            if time.monotonic() >= deadline:
                proc.kill()
                proc.wait(timeout=0.5)
                raise PCControlError("Timed out reading screen controls.")
            time.sleep(0.05)

        out, err = proc.communicate(timeout=0.5)
        if stop_check and stop_check():
            raise StoppedError("Stopped while reading UI controls.")
        if proc.returncode != 0:
            msg = (err or out or "").strip() or f"exit {proc.returncode}"
            raise PCControlError(f"UI Automation failed: {msg[:300]}")
        return out
    finally:
        with _PROC_LOCK:
            _ACTIVE_PROCS.discard(proc)


def _ps_quote(value: str) -> str:
    return "'" + (value or "").replace("'", "''") + "'"


def _build_read_script(window_title: str, limit: int) -> str:
    qtitle = _ps_quote(window_title.strip())
    lim = max(1, min(int(limit), 200))
    return f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -MemberDefinition '[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();' -Name WinFG -Namespace PromUIA -ErrorAction SilentlyContinue
$uia = [System.Windows.Automation.AutomationElement]
$condTrue = [System.Windows.Automation.Condition]::TrueCondition
$targetTitle = {qtitle}
$root = $null
if ($targetTitle) {{
    $wins = $uia::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $condTrue)
    for ($i = 0; $i -lt $wins.Count; $i++) {{
        $w = $wins.Item($i)
        if ($w.Current.Name -and $w.Current.Name.ToLower().Contains($targetTitle.ToLower())) {{
            $root = $w; break
        }}
    }}
}} else {{
    $fg = [PromUIA.WinFG]::GetForegroundWindow()
    if ($fg -ne [IntPtr]::Zero) {{
        try {{ $root = $uia::FromHandle($fg) }} catch {{ $root = $null }}
    }}
}}
if ($null -eq $root) {{ $root = $uia::RootElement }}
$focusedEl = $null
try {{ $focusedEl = $uia::FocusedElement }} catch {{ $focusedEl = $null }}
$focusedRuntime = if ($focusedEl) {{ $focusedEl.GetRuntimeId() -join ',' }} else {{ '' }}
function ShortType($ct) {{
    if ($null -eq $ct) {{ return '' }}
    return ($ct.ProgrammaticName -replace '^ControlType\\.', '')
}}
$items = New-Object System.Collections.Generic.List[object]
$all = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condTrue)
$max = [Math]::Min($all.Count, {lim})
for ($i = 0; $i -lt $max; $i++) {{
    $el = $all.Item($i)
    try {{
        $c = $el.Current
        $isPwd = [bool]$c.IsPassword
        $val = ''
        if (-not $isPwd) {{
            try {{
                $vp = $el.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
                if ($vp -and $vp.Current.Value) {{
                    $s = [string]$vp.Current.Value
                    if ($s.Length -gt 120) {{ $s = $s.Substring(0, 120) }}
                    $val = $s
                }}
            }} catch {{}}
        }}
        $r = $c.BoundingRectangle
        $rect = if ([double]::IsInfinity($r.Left) -or [double]::IsNaN($r.Left)) {{ @(0,0,0,0) }} else {{ @([int]$r.Left, [int]$r.Top, [int]$r.Right, [int]$r.Bottom) }}
        $rt = ''
        try {{ $rt = ($el.GetRuntimeId() -join ',') }} catch {{}}
        $items.Add([pscustomobject]@{{
            name = [string]$c.Name
            control_type = (ShortType $c.ControlType)
            automation_id = [string]$c.AutomationId
            rect = $rect
            focused = ($focusedRuntime -ne '' -and $rt -eq $focusedRuntime)
            enabled = [bool]$c.IsEnabled
            is_password = $isPwd
            value = $val
        }})
    }} catch {{}}
}}
$focusedInfo = $null
if ($focusedEl) {{
    try {{
        $fc = $focusedEl.Current
        $focusedInfo = [pscustomobject]@{{
            name = [string]$fc.Name
            control_type = (ShortType $fc.ControlType)
            automation_id = [string]$fc.AutomationId
            is_password = [bool]$fc.IsPassword
        }}
    }} catch {{}}
}}
$winName = ''
try {{ $winName = [string]$root.Current.Name }} catch {{ $winName = '' }}
$outObj = [pscustomobject]@{{
    window = $winName
    focused = $focusedInfo
    controls = $items.ToArray()
}}
ConvertTo-Json -InputObject $outObj -Depth 4 -Compress
"""


def _build_focused_script() -> str:
    return """
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient
$el = $null
try { $el = [System.Windows.Automation.AutomationElement]::FocusedElement } catch { $el = $null }
$res = if ($null -eq $el) {
    [pscustomobject]@{ name=''; control_type=''; automation_id=''; is_password=$false }
} else {
    $c = $el.Current
    $ct = if ($c.ControlType) { ($c.ControlType.ProgrammaticName -replace '^ControlType\\.', '') } else { '' }
    [pscustomobject]@{
        name = [string]$c.Name
        control_type = $ct
        automation_id = [string]$c.AutomationId
        is_password = [bool]$c.IsPassword
    }
}
ConvertTo-Json -InputObject $res -Compress
"""


def _clean_control(raw: dict[str, Any]) -> ControlInfo:
    is_pwd = bool(raw.get("is_password"))
    name = str(raw.get("name") or "")[:120]
    ctype = str(raw.get("control_type") or "")[:40]
    aid = str(raw.get("automation_id") or "")[:80]
    if _looks_like_password_meta(name, ctype, aid):
        is_pwd = True
    rect_raw = raw.get("rect") or [0, 0, 0, 0]
    rect = (
        int(rect_raw[0]) if len(rect_raw) > 0 else 0,
        int(rect_raw[1]) if len(rect_raw) > 1 else 0,
        int(rect_raw[2]) if len(rect_raw) > 2 else 0,
        int(rect_raw[3]) if len(rect_raw) > 3 else 0,
    )
    value = "" if is_pwd else str(raw.get("value") or "")[:120]
    return ControlInfo(
        name=name,
        control_type=ctype,
        automation_id=aid,
        rect=rect,
        focused=bool(raw.get("focused")),
        enabled=bool(raw.get("enabled", True)),
        is_password=is_pwd,
        value=value,
    )


def _looks_like_password_meta(name: str, control_type: str, automation_id: str) -> bool:
    ctype_low = (control_type or "").lower()
    if ctype_low not in {"edit", "document", "text", "custom", ""}:
        return False
    combined = f"{name} {automation_id}".strip()
    return bool(_PASSWORD_HINT_RE.search(combined))


def _parse_json_output(raw_text: str) -> dict[str, Any]:
    txt = (raw_text or "").strip()
    if not txt:
        raise PCControlError("UI Automation returned no output.")
    try:
        data = json.loads(txt)
    except json.JSONDecodeError as exc:
        raise PCControlError(f"Could not parse UI Automation output: {exc}") from exc
    if not isinstance(data, dict):
        raise PCControlError("Unexpected UI Automation payload.")
    return data


def read_controls(
    window_title: str = "",
    limit: int = 80,
    *,
    stop_check: Callable[[], bool] | None = None,
    ps_runner: Callable[..., str] | None = None,
) -> dict[str, Any]:
    """Read accessible controls from the foreground or matching window."""
    runner = ps_runner or run_ps_abortable
    script = _build_read_script(window_title, limit)
    raw_text = runner(script, stop_check=stop_check)
    data = _parse_json_output(raw_text)
    raw_controls = data.get("controls") or []
    if isinstance(raw_controls, dict):
        raw_controls = [raw_controls]
    controls = [_clean_control(item) for item in raw_controls if isinstance(item, dict)]
    focused = data.get("focused")
    if isinstance(focused, dict):
        focused = {
            "name": str(focused.get("name") or "")[:120],
            "control_type": str(focused.get("control_type") or "")[:40],
            "automation_id": str(focused.get("automation_id") or "")[:80],
            "is_password": bool(focused.get("is_password"))
            or _looks_like_password_meta(
                str(focused.get("name") or ""),
                str(focused.get("control_type") or ""),
                str(focused.get("automation_id") or ""),
            ),
        }
    return {
        "window": str(data.get("window") or "")[:160],
        "focused": focused,
        "controls": controls,
    }


def focused_is_password(
    *,
    stop_check: Callable[[], bool] | None = None,
    ps_runner: Callable[..., str] | None = None,
) -> tuple[bool, str]:
    """Check whether the currently focused UI element is a password field."""
    runner = ps_runner or run_ps_abortable
    raw_text = runner(_build_focused_script(), stop_check=stop_check)
    data = _parse_json_output(raw_text)
    if bool(data.get("is_password")):
        return True, "focused control is marked IsPassword"
    name = str(data.get("name") or "")
    ctype = str(data.get("control_type") or "")
    aid = str(data.get("automation_id") or "")
    if _looks_like_password_meta(name, ctype, aid):
        label = name or aid or ctype
        return True, f"focused field looks like a password field ({label})"
    return False, ""
