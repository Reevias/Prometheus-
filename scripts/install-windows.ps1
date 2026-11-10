# Installs (or updates) the new Prometheus and makes the desktop shortcut.
# Run in PowerShell:
#   irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/install-windows.ps1 | iex
# or, from a copy of the repository:
#   powershell -ExecutionPolicy Bypass -File scripts\install-windows.ps1
$ErrorActionPreference = "Stop"
$Repo = "https://github.com/Reevias/Prometheus-.git"
$Dir = Join-Path $env:USERPROFILE "Prometheus-"

if (-not (Get-Command git -ErrorAction SilentlyContinue)) { throw "Git is not installed. Get it from https://git-scm.com/download/win" }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw "Python is not installed. Get Python 3.12 or newer from https://www.python.org/downloads/" }

# Newest installed Python that is 3.12 or later.
$Py = $null
foreach ($v in "3.14", "3.13", "3.12") {
    & py "-$v" -c "import sys" 2>$null
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

$Settings = Join-Path $env:LOCALAPPDATA "Prometheus\settings.json"
$HasPassword = (Test-Path $Settings) -and ((Get-Content $Settings -Raw) -match '"password_hash":\s*"[^"]+"')
if (-not $HasPassword) { & $Python -m prometheus.cli init }

& $Python -m prometheus.cli shortcut
& $Python -m prometheus.cli doctor
Write-Host ""
Write-Host "Done. Open Prometheus with the Prometheus shortcut on your desktop or in the Start menu."
Write-Host "To update later: open Prometheus and ask it to update itself, or run this script again."
