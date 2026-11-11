; Prometheus-Setup.exe: a normal Windows installer (Next, Next, Finish), built by GitHub Actions.
; It needs no administrator rights. It runs scripts\install-windows.ps1, which gets Git and Python if missing,
; downloads Prometheus to %USERPROFILE%\Prometheus-, and makes the Start-menu and desktop shortcuts with the icon.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

[Setup]
AppId={{6A0E5B7C-2F4D-4C8E-9B1A-50F0C1E2D3A4}
AppName=Prometheus
AppVersion={#AppVersion}
AppPublisher=Reevias
AppPublisherURL=https://github.com/Reevias/Prometheus-
DefaultDirName={localappdata}\Programs\Prometheus Setup
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist
OutputBaseFilename=Prometheus-Setup
SetupIconFile=..\prometheus\data\prometheus.ico
UninstallDisplayIcon={app}\prometheus.ico
UninstallDisplayName=Prometheus
WizardStyle=modern
Compression=lzma2
SolidCompression=yes

[Files]
Source: "..\scripts\install-windows.ps1"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\prometheus\data\prometheus.ico"; DestDir: "{app}"; Flags: ignoreversion

[Run]
Filename: "powershell.exe"; Parameters: "-NoProfile -ExecutionPolicy Bypass -Command ""& '{app}\install-windows.ps1'; Read-Host 'All done. Press Enter to close this window'"""; \
  StatusMsg: "Installing Prometheus (a window shows the progress; it asks you to choose a password)..."; \
  Flags: waituntilterminated

[UninstallDelete]
Type: files; Name: "{userdesktop}\Prometheus.lnk"
Type: files; Name: "{userprograms}\Prometheus.lnk"
Type: files; Name: "{userstartup}\Prometheus (background).lnk"

[Messages]
FinishedLabel=Prometheus is installed. Open it from the Start menu or the desktop shortcut.%n%nYour memory and settings are kept in %LOCALAPPDATA%\Prometheus. Uninstalling removes the shortcuts but keeps them.
