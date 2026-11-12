# Removes Prometheus from this laptop: closes it, deletes the program folder, the shortcuts and the start-with-Windows
# entry. Your memory, chats, settings and keys are KEPT (in %LOCALAPPDATA%\Prometheus and
# %USERPROFILE%\Prometheus memory), so a new install picks up where you left off.
#
#   irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/uninstall-windows.ps1 | iex
#
# To delete everything, your data too, set this first in the same window:   $DeleteMyData = $true
$ErrorActionPreference = "Continue"

Write-Host "Closing Prometheus..."
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -like "python*" -and $_.CommandLine -match "prometheus\.cli" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2

$Links = @(
    (Join-Path ([Environment]::GetFolderPath("Desktop")) "Prometheus.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Prometheus.lnk"),
    (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\Startup\Prometheus (background).lnk")
)
foreach ($l in $Links) { if (Test-Path $l) { Remove-Item -Force $l; Write-Host "Removed $l" } }

# the program itself. Only Prometheus's own folder, and only if it really is Prometheus. The older copy inside
# %USERPROFILE%\Claude-testing- is left alone: that folder can hold your other work.
foreach ($d in @((Join-Path $env:USERPROFILE "Prometheus-"))) {
    if ((Test-Path (Join-Path $d "pyproject.toml")) -and (Test-Path (Join-Path $d "prometheus\cli.py"))) {
        Remove-Item -Recurse -Force $d -ErrorAction SilentlyContinue
        if (Test-Path $d) { Write-Host "Couldn't remove all of $d (a file is open?). Restart Windows and run this again." -ForegroundColor Yellow }
        else { Write-Host "Removed $d" }
    }
}

# Prometheus-Setup.exe's own entry in Apps, if it was installed that way
$Setup = Join-Path $env:LOCALAPPDATA "Programs\Prometheus Setup"
$Uninst = Get-ChildItem $Setup -Filter "unins*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
if ($Uninst) { Start-Process $Uninst.FullName -ArgumentList "/VERYSILENT", "/NORESTART" -Wait; Write-Host "Removed the Setup entry from Apps" }

if ($DeleteMyData) {
    foreach ($d in @((Join-Path $env:LOCALAPPDATA "Prometheus"), (Join-Path $env:USERPROFILE "Prometheus memory"))) {
        if (Test-Path $d) { Remove-Item -Recurse -Force $d; Write-Host "Deleted $d" }
    }
    Write-Host "Prometheus and all its data are gone."
} else {
    Write-Host ""
    Write-Host "Prometheus is removed. Your memory, chats, settings and keys are kept for the next install."
}
Write-Host "Install the newest version with:"
Write-Host "  irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/install-windows.ps1 | iex"
