# Installs (or updates) Prometheus and makes the Start-menu and desktop shortcuts.
# One line, in PowerShell:
#   irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/install-windows.ps1 | iex
# It also gets Git and Python by itself (through winget) when they're missing. Prometheus-Setup.exe runs this too.
$ErrorActionPreference = "Stop"
# If something goes wrong, say what and keep the window open so it can be read.
trap { Write-Host ""; Write-Host "Prometheus could not be installed: $_" -ForegroundColor Red; Read-Host "Press Enter to close"; break }
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
Get-Tool py "Python.Python.3.14"

# A Prometheus that is open keeps running the old code (its buttons would say "Not Found"): close it first.
$Running = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "python*" -and $_.CommandLine -match "prometheus\.cli" }
if ($Running) {
    Write-Host "Closing the running Prometheus so it can be updated..."
    $Running | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Seconds 2
}

# Python 3.14 (every part of Prometheus, voice and photos included, has a Windows build for it). 3.15 works too,
# except voice and photo reading: one part they need (onnxruntime) isn't made for 3.15 yet.
function Test-Py($v) { try { & py "-$v" -c "import sys" 2>$null } catch { return $false }; return ($LASTEXITCODE -eq 0) }
$Py = $null
if (Test-Py "3.14") { $Py = "3.14" }
else {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Host "Installing Python 3.14 (one time)..."
        winget install --id Python.Python.3.14 -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
        Refresh-Path
    }
    if (Test-Py "3.14") { $Py = "3.14" }
    elseif (Test-Py "3.15") { $Py = "3.15"; Write-Host "Using Python 3.15: voice and photo reading need Python 3.14 for now." -ForegroundColor Yellow }
}
if (-not $Py) { throw "Prometheus needs Python 3.14. Install it from https://www.python.org/downloads/ (or: py install 3.14), then run this again." }
Write-Host "Using Python $Py"

if (Test-Path (Join-Path $Dir ".git")) {
    Write-Host "Updating $Dir"
    git -C $Dir pull --ff-only origin main
} else {
    Write-Host "Downloading Prometheus to $Dir"
    git clone $Repo $Dir
}

$Venv = Join-Path $Dir ".venv"
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if (Test-Path $VenvPy) {
    # a Prometheus set up with an older Python (3.12, 3.13) is moved to the new one; your data is not touched
    $Have = (& $VenvPy -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')" 2>$null)
    if ($Have -ne $Py) { Write-Host "Moving Prometheus from Python $Have to Python $Py..."; Remove-Item -Recurse -Force $Venv }
}
if (-not (Test-Path $VenvPy)) { & py "-$Py" -m venv $Venv }
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
# the laptop voice: its label still says "up to Python 3.13", but it works on 3.14 (its parts are installed above)
if ($VoiceOk) { & $Python -m pip install --quiet --no-deps --ignore-requires-python "kokoro-onnx>=0.4"; $VoiceOk = ($LASTEXITCODE -eq 0) }
if (-not $VoiceOk) { Write-Host "Voice packages could not be installed on Python $Py. Prometheus works without voice; Python 3.14 fixes this." }

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
