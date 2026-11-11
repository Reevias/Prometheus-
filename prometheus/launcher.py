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
# Besides the icon, the shortcut gets the same app id the Prometheus window uses (System.AppUserModel.ID). Without
# it, Windows search and the taskbar treat the shortcut as "a Python program" and show Python's icon.
SHORTCUT_PS = (
    "Remove-Item -LiteralPath $env:PROM_LINK -ErrorAction SilentlyContinue; "
    "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($env:PROM_LINK); "
    "$s.TargetPath = $env:PROM_TARGET; $s.Arguments = $env:PROM_ARGS; "
    "$s.WorkingDirectory = $env:PROM_DIR; $s.Description = 'Prometheus, your AI brain'; "
    "if ($env:PROM_ICON) { $s.IconLocation = $env:PROM_ICON + ',0' }; $s.Save(); "
    "if ($env:PROM_APPID) { try { Add-Type -TypeDefinition @'\n"
    "using System; using System.Runtime.InteropServices;\n"
    "[ComImport, Guid(\"886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99\"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]\n"
    "public interface IPropStore { int GetCount(out uint c); int GetAt(uint i, out PKey k); "
    "int GetValue(ref PKey k, IntPtr v); int SetValue(ref PKey k, ref PVar v); int Commit(); }\n"
    "[StructLayout(LayoutKind.Sequential, Pack = 4)] public struct PKey { public Guid fmtid; public uint pid; }\n"
    "[StructLayout(LayoutKind.Explicit, Size = 24)] public struct PVar { [FieldOffset(0)] public ushort vt; "
    "[FieldOffset(8)] public IntPtr p; }\n"
    "public static class PromAppId {\n"
    "  [DllImport(\"shell32.dll\", CharSet = CharSet.Unicode)] static extern int SHGetPropertyStoreFromParsingName("
    "string path, IntPtr bc, int flags, ref Guid iid, out IPropStore ps);\n"
    "  public static void Set(string lnk, string id) {\n"
    "    Guid iid = new Guid(\"886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99\"); IPropStore ps;\n"
    "    Marshal.ThrowExceptionForHR(SHGetPropertyStoreFromParsingName(lnk, IntPtr.Zero, 2, ref iid, out ps));\n"
    "    PKey k = new PKey(); k.fmtid = new Guid(\"9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3\"); k.pid = 5;\n"
    "    PVar v = new PVar(); v.vt = 31; v.p = Marshal.StringToCoTaskMemUni(id);\n"
    "    try { Marshal.ThrowExceptionForHR(ps.SetValue(ref k, ref v)); Marshal.ThrowExceptionForHR(ps.Commit()); }\n"
    "    finally { Marshal.FreeCoTaskMem(v.p); Marshal.ReleaseComObject(ps); }\n"
    "  }\n"
    "}\n"
    "'@ -ErrorAction SilentlyContinue; [PromAppId]::Set($env:PROM_LINK, $env:PROM_APPID) } catch { } }; "
    "Start-Process -FilePath ie4uinit.exe -ArgumentList '-show' -WindowStyle Hidden -ErrorAction SilentlyContinue"
)


def version(code_dir: Path | None = None) -> str:
    """This copy's version, read from its pyproject.toml (so it's right straight after an update)."""
    import re as _re

    try:
        text = (Path(code_dir or CODE_DIR) / "pyproject.toml").read_text(encoding="utf-8")
        m = _re.search(r'^version\s*=\s*"([^"]+)"', text, _re.M)
        if m:
            return m.group(1)
    except OSError:
        pass
    try:
        return metadata.version("prometheus")
    except metadata.PackageNotFoundError:
        return "dev"


def update_report(code_dir: Path | None = None, run=None, fetch: bool = True) -> dict:
    """This version and the newest on GitHub: {'current', 'commit', 'date', 'latest', 'behind', 'news', 'error'}."""
    import re as _re

    code_dir = Path(code_dir or CODE_DIR)
    run = run or subprocess.run
    out = {"current": version(code_dir), "commit": "", "date": "", "latest": "", "behind": 0, "news": [], "error": ""}

    def git(*args: str) -> str:
        proc = run(["git", "-C", str(code_dir), *args], capture_output=True, text=True, encoding="utf-8",
                   errors="replace", timeout=60)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or "").strip()[-200:] or "git failed")
        return (proc.stdout or "").strip()

    if not (code_dir / ".git").exists():
        out["error"] = "This copy isn't a Git checkout, so it can't check for updates."
        return out
    try:
        out["commit"], out["date"] = git("log", "-1", "--format=%h|%cd", "--date=format:%d %b %Y").split("|")
        if fetch:
            git("fetch", "--quiet", "origin", "main")
        out["behind"] = int(git("rev-list", "--count", "HEAD..origin/main") or 0)
        remote = git("show", "origin/main:pyproject.toml")
        m = _re.search(r'^version\s*=\s*"([^"]+)"', remote, _re.M)
        out["latest"] = m.group(1) if m else out["current"]
        if out["behind"]:
            mine = set(git("show", "HEAD:CHANGELOG.md").splitlines())
            theirs = git("show", "origin/main:CHANGELOG.md").splitlines()
            out["news"] = [l[2:] for l in theirs if l.startswith("- ") and l not in mine][:8]
    except Exception as e:                      # offline, or GitHub sign-in needed
        out["error"] = f"Couldn't check GitHub ({str(e)[:120]})."
    return out


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


ICON = Path(__file__).resolve().parent / "data" / "prometheus.ico"
APP_ID = "Reevias.Prometheus"           # how Windows tells Prometheus apart from other Python programs


def stable_icon() -> Path:
    """A copy of the icon in %LOCALAPPDATA%\\Prometheus, so shortcuts keep it even if the code folder moves."""
    local = Path(os.environ.get("LOCALAPPDATA") or Path(os.environ.get("USERPROFILE") or Path.home())
                 / "AppData" / "Local") / "Prometheus"
    try:
        local.mkdir(parents=True, exist_ok=True)
        target = local / "prometheus.ico"
        data = ICON.read_bytes()
        if not target.exists() or target.read_bytes() != data:
            target.write_bytes(data)
        return target
    except OSError:
        return ICON


def make_shortcuts(run=None, windows: bool | None = None) -> list[Path]:
    """Desktop and Start-menu shortcuts that always open THIS copy (replacing any older Prometheus shortcut)."""
    if not (os.name == "nt" if windows is None else windows):
        raise RuntimeError("Shortcuts are made on Windows only. Start Prometheus with:  prometheus app")
    run = run or subprocess.run
    made = []
    for link in shortcut_paths():
        link.parent.mkdir(parents=True, exist_ok=True)
        env = {**os.environ, "PROM_LINK": str(link), "PROM_TARGET": pythonw(), "PROM_DIR": str(CODE_DIR),
               "PROM_ARGS": "-m prometheus.cli app", "PROM_ICON": str(stable_icon()), "PROM_APPID": APP_ID}
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
           "PROM_ARGS": "-m prometheus.cli background", "PROM_ICON": str(stable_icon())}
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
               encoding="utf-8", errors="replace", timeout=180)
    if pull.returncode != 0:
        raise RuntimeError("The update did not go through (you may have local changes). " + (pull.stderr or "")[-300:])
    extras = []
    for module, extra in (("webview", "desktop"), ("vosk", "voice"), ("playwright", "browser"),
                          ("fastembed", "photos")):   # keep the extras this laptop already uses
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
