# Installs (or updates) Prometheus and makes the Start-menu and desktop shortcuts.
# One line, in PowerShell:
#   irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/install-windows.ps1 | iex
# It also gets Git and Python by itself (through winget) when they're missing. Prometheus-Setup.exe runs this too.
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/Reevias/Prometheus-.git"
$Dir = Join-Path $env:USERPROFILE "Prometheus-"

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Get-Tool($Name, $WingetId) {
    if (Get-Command $Name -ErrorAction SilentlyContinue) { return }
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "$Name is missing and winget isn't available to get it. Install $Name, then run this again."
    }
    Write-Host "Installing $Name (one time)..."
    winget install --id $WingetId -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    Refresh-Path
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) { throw "$Name was installed but isn't found yet. Close this window, open a new one and run the line again." }
}
Get-Tool git "Git.Git"
Get-Tool py "Python.Python.3.12"

# Newest installed Python that is 3.12 or later.
$Py = $null
foreach ($v in "3.14", "3.13", "3.12") {
    # A missing version writes to stderr; in Windows PowerShell 5.1 with "Stop" that would end the script, so catch it.
    try { & py "-$v" -c "import sys" 2>$null } catch { continue }
    if ($LASTEXITCODE -eq 0) { $Py = $v; break }
}
if (-not $Py) { throw "Prometheus needs Python 3.12 or newer. Install it from https://www.python.org/downloads/" }
Write-Host "Using Python $Py"

if (Test-Path (Join-Path $Dir ".git")) {
    Write-Host "Updating $Dir"
    git -C $Dir pull --ff-only origin main
} else {
    Write-Host "Downloading Prometheus to $Dir"
    git clone $Repo $Dir
}

$Venv = Join-Path $Dir ".venv"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) { & py "-$Py" -m venv $Venv }
$Python = Join-Path $Venv "Scripts\python.exe"
& $Python -m pip install --quiet --upgrade pip
& $Python -m pip install --quiet -e "$Dir[desktop]"
# Browser control (uses Microsoft Edge, already on Windows): optional too.
& $Python -m pip install --quiet -e "$Dir[desktop,browser]"
# Photos (what's in them, text in them; models download once, about 600 MB): optional too.
& $Python -m pip install --quiet -e "$Dir[desktop,browser,photos]"
# Phone control (Android wireless debugging): the adb tool, optional.
if (-not (Get-Command adb -ErrorAction SilentlyContinue)) { winget install --id Google.PlatformTools -e --silent --accept-package-agreements --accept-source-agreements 2>$null | Out-Null }
# Voice (say "Prometheus"): optional, so a failed voice install never stops Prometheus itself.
& $Python -m pip install --quiet -e "$Dir[desktop,browser,voice]"
$VoiceOk = ($LASTEXITCODE -eq 0)
if (-not $VoiceOk) { Write-Host "Voice packages could not be installed on Python $Py. Prometheus works without voice; Python 3.12 or 3.13 usually fixes this." }

$Settings = Join-Path $env:LOCALAPPDATA "Prometheus\settings.json"
$HasPassword = (Test-Path $Settings) -and ((Get-Content $Settings -Raw) -match '"password_hash":\s*"[^"]+"')
if (-not $HasPassword) { & $Python -m prometheus.cli init }

& $Python -m prometheus.cli shortcut
if ($VoiceOk) {
    & $Python -m prometheus.cli voice-setup      # downloads about 250 MB of voice models, once
    & $Python -m prometheus.cli autostart on     # starts with Windows, listening, without a window
    $Quiet = Join-Path $Venv "Scripts\pythonw.exe"
    Start-Process -FilePath $Quiet -ArgumentList "-m", "prometheus.cli", "background" -WindowStyle Hidden
    Write-Host 'Voice is on: say "Prometheus" and then what you want.'
}
& $Python -m prometheus.cli doctor
Write-Host ""
Write-Host "Done. Open Prometheus with the Prometheus shortcut on your desktop or in the Start menu."
Write-Host "To update later: open Prometheus and ask it to update itself, or run this script again."
