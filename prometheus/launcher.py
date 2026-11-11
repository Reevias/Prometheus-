"""Keeps the laptop on the newest Prometheus: version info, an update command and desktop shortcuts."""
from __future__ import annotations

import os
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import prometheus

CODE_DIR = Path(prometheus.__file__).resolve().parent.parent
HOME_REPO = "Prometheus-"            # the main repository; anything else is an old copy
OLD_REPOS = ("Claude-testing-",)
SHORTCUT_NAME = "Prometheus.lnk"

# Fixed script: the paths travel in environment variables, never inside the command text.
SHORTCUT_PS = (
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PROM_LINK); "
    "$s.TargetPath = $env:PROM_TARGET; $s.Arguments = $env:PROM_ARGS; "
    "$s.WorkingDirectory = $env:PROM_DIR; $s.Description = 'Prometheus, your AI brain'; $s.Save()"
)


def version() -> str:
    try:
        return metadata.version("prometheus")
    except metadata.PackageNotFoundError:
        return "dev"


def remote_url(code_dir: Path = CODE_DIR) -> str:
    if not (code_dir / ".git").exists():
        return ""
    try:
        proc = subprocess.run(["git", "-C", str(code_dir), "remote", "get-url", "origin"], capture_output=True,
                              text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def is_old_copy(code_dir: Path = CODE_DIR, url: str | None = None) -> bool:
    """True when this Prometheus was installed from the old repository (or its folder)."""
    url = remote_url(code_dir) if url is None else url
    where = f"{url} {code_dir}".lower()
    return any(old.lower() in where for old in OLD_REPOS)


def old_copy_warning(code_dir: Path = CODE_DIR, url: str | None = None) -> str:
    if not is_old_copy(code_dir, url):
        return ""
    return ("This is the OLD copy of Prometheus (from the Claude-testing- repository). The new one lives in the "
            "Prometheus- repository. Install it with scripts\\install-windows.ps1 from that repository, then use the "
            "Prometheus shortcut on your desktop.")


def pythonw() -> str:
    """The windowless Python next to this one, so the desktop app opens without a black console."""
    exe = Path(sys.executable)
    quiet = exe.with_name("pythonw.exe")
    return str(quiet if quiet.exists() else exe)


def shortcut_paths() -> list[Path]:
    home = Path(os.environ.get("USERPROFILE") or Path.home())
    appdata = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
    return [home / "Desktop" / SHORTCUT_NAME,
            appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / SHORTCUT_NAME]


def make_shortcuts(run=None, windows: bool | None = None) -> list[Path]:
    """Desktop and Start-menu shortcuts that always open THIS copy (replacing any older Prometheus shortcut)."""
    if not (os.name == "nt" if windows is None else windows):
        raise RuntimeError("Shortcuts are made on Windows only. Start Prometheus with:  prometheus app")
    run = run or subprocess.run
    made = []
    for link in shortcut_paths():
        link.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PROM_LINK": str(link), "PROM_TARGET": pythonw(), "PROM_DIR": str(CODE_DIR),
               "PROM_ARGS": "-m prometheus.cli app"}
        proc = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SHORTCUT_PS], env=env,
                   capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            made.append(link)
    return made


def startup_link() -> Path:
    appdata = Path(os.environ.get("APPDATA") or Path(os.environ.get("USERPROFILE") or Path.home()) / "AppData" / "Roaming")
    return appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "Prometheus (background).lnk"


def set_autostart(on: bool, run=None, windows: bool | None = None) -> str:
    """Start Prometheus quietly (no window, listening for its name) when you sign in to Windows."""
    if not (os.name == "nt" if windows is None else windows):
        raise RuntimeError("Starting with Windows only works on Windows.")
    link = startup_link()
    if not on:
        link.unlink(missing_ok=True)
        return "Prometheus will no longer start with Windows."
    run = run or subprocess.run
    link.parent.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "PROM_LINK": str(link), "PROM_TARGET": pythonw(), "PROM_DIR": str(CODE_DIR),
           "PROM_ARGS": "-m prometheus.cli background"}
    proc = run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", SHORTCUT_PS], env=env,
               capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError("Could not add Prometheus to Windows startup.")
    return "Prometheus will start with Windows and listen for its name."


def update(code_dir: Path = CODE_DIR, run=None) -> str:
    """Download the newest version from main (fast-forward only) and install any new packages it needs."""
    if not (code_dir / ".git").exists():
        raise RuntimeError("This copy of Prometheus is not a Git checkout, so it can't update itself.")
    run = run or subprocess.run
    pull = run(["git", "-C", str(code_dir), "pull", "--ff-only", "origin", "main"], capture_output=True, text=True,
               timeout=180)
    if pull.returncode != 0:
        raise RuntimeError("The update did not go through (you may have local changes). " + (pull.stderr or "")[-300:])
    extras = []
    for module, extra in (("webview", "desktop"), ("vosk", "voice"), ("playwright", "browser")):   # keep the extras this laptop already uses
        try:
            __import__(module)
            extras.append(extra)
        except Exception:
            pass
    target = str(code_dir) + (f"[{','.join(extras)}]" if extras else "")
    pip = run([sys.executable, "-m", "pip", "install", "--quiet", "-e", target], capture_output=True, text=True,
              timeout=900)
    if pip.returncode != 0:
        raise RuntimeError("Downloaded the new code but installing its packages failed: " + (pip.stderr or "")[-300:])
    return "Updated to the newest version. Restart Prometheus to use it.\n" + (pull.stdout or "")[-300:]
