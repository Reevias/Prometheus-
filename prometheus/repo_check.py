"""Checks a downloaded repository for malware before Prometheus keeps it (it is never run either way).

Two checks:
1. Microsoft Defender scans the folder (on Windows; it's built in), the same scan as right-clicking a folder.
2. Prometheus's own look for what malicious repos commonly hide: code that runs when installed (postinstall,
   setup.py downloads), download-and-run lines, hidden or encoded code, programs and scripts shipped inside,
   stealing browser passwords, cookies, Discord tokens or crypto wallets, sending data to webhooks, reverse
   shells, crypto miners, starting with Windows, and turning off antivirus.

Verdict: "danger" (deleted at once), "caution" (kept, with what to look at) or "clean". No scanner catches
everything; "clean" means nothing known-bad was found, not a promise.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

MAX_FILES = 6000
MAX_BYTES = 2_000_000
TEXT_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".ps1", ".psm1", ".bat", ".cmd", ".vbs", ".json",
            ".toml", ".cfg", ".ini", ".yml", ".yaml", ".rb", ".go", ".rs", ".php", ".lua", ".pl", ".txt", ".md", ""}
BINARY_DANGER = {".exe", ".scr", ".com", ".pif", ".msi", ".hta", ".dll", ".sys", ".jar", ".vbe", ".jse", ".lnk"}
SCRIPT_CAUTION = {".bat", ".cmd", ".ps1", ".vbs", ".wsf", ".sh"}

DANGER = [
    ("downloads and runs code", r"(curl|wget)[^\n|]{0,200}\|\s*(sudo\s+)?(ba)?sh\b|"
                                r"(iwr|irm|invoke-webrequest|invoke-restmethod|downloadstring)[^\n]{0,200}\|\s*iex\b|"
                                r"iex\s*\(\s*\(?new-object\s+net\.webclient"),
    ("runs hidden PowerShell", r"powershell[^\n]{0,40}\s-(e|enc|encodedcommand)\s+[A-Za-z0-9+/=]{20,}"),
    ("runs encoded code", r"(exec|eval)\s*\(\s*(base64\.b64decode|__import__\(\s*['\"]base64|zlib\.decompress|"
                          r"marshal\.loads|bytes\.fromhex|codecs\.decode)|"
                          r"eval\s*\(\s*(atob|Buffer\.from)\s*\(|new\s+Function\s*\(\s*atob"),
    ("steals browser passwords or cookies", r"Login Data|Local State['\"]?\s*\)|\\Cookies['\"]|Network\\\\Cookies|"
                                            r"CryptUnprotectData|os_crypt.{0,40}encrypted_key"),
    ("steals Discord tokens", r"discord(app)?[^\n]{0,80}(leveldb|Local Storage)|dQw4w9WgXcQ:"),
    ("steals crypto wallets", r"wallet\.dat|Exodus\\\\exodus\.wallet|Electrum\\\\wallets|MetaMask[^\n]{0,40}"
                              r"(Local Extension Settings|nkbihfbeogaeaoehlefnkodbefgpgknn)"),
    ("sends data to a Discord webhook", r"discord(app)?\.com/api/webhooks/\d+/"),
    ("opens a reverse shell", r"/dev/tcp/\d|nc(at)?\s+-e\s+/bin/|socket\.socket\([^\n]{0,200}\n[^\n]{0,200}"
                              r"(subprocess|pty\.spawn|os\.dup2)|os\.dup2\(\s*s\.fileno"),
    ("mines crypto", r"stratum\+tcp://|xmrig|minexmr|nicehash|cryptonight"),
    ("turns off antivirus", r"Set-MpPreference[^\n]{0,80}Disable|DisableRealtimeMonitoring|Add-MpPreference\s+-Exclusion"),
    ("starts itself with Windows", r"CurrentVersion\\\\Run\b|schtasks(\.exe)?\s+/create|\\Start Menu\\\\Programs\\\\Startup"),
    ("records the keyboard", r"pynput\.keyboard[^\n]{0,200}Listener[\s\S]{0,400}(requests\.post|smtplib|webhook)|"
                             r"SetWindowsHookEx(A|W)?\s*\(\s*13"),
]
CAUTION = [
    ("runs code when installed", None),        # package.json hooks and setup.py are checked separately
    ("contacts a raw IP address", r"https?://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?/"),
    ("uses paste or file-drop sites", r"pastebin\.com/raw|transfer\.sh|anonfiles|gofile\.io|ngrok\.io"),
    ("very long single lines (often hidden code)", None),
]


@dataclass
class Report:
    verdict: str = "clean"
    findings: list[tuple[str, str, str]] = field(default_factory=list)     # (level, what, where)
    defender: str = ""
    files: int = 0

    def add(self, level: str, what: str, where: str) -> None:
        if len(self.findings) < 60 and (level, what, where) not in self.findings:
            self.findings.append((level, what, where))
        if level == "danger":
            self.verdict = "danger"
        elif level == "caution" and self.verdict == "clean":
            self.verdict = "caution"

    def describe(self) -> str:
        head = {"danger": "⚠️ Dangerous: deleted, nothing was run.", "caution": "Looks mostly fine, with things to check:",
                "clean": "No malware found."}[self.verdict]
        lines = [f"{head} ({self.files} files checked" + (f"; Defender: {self.defender}" if self.defender else "")
                 + ")"]
        for level, what, where in self.findings[:12]:
            lines.append(f"- {'✗' if level == 'danger' else '•'} {what}: {where}")
        if self.verdict == "clean":
            lines.append("No scanner catches everything, so it's still never run without asking you.")
        return "\n".join(lines)


def _defender(folder: Path) -> str:
    exe = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Windows Defender" / "MpCmdRun.exe"
    if os.name != "nt" or not exe.exists():
        return ""
    try:
        proc = subprocess.run([str(exe), "-Scan", "-ScanType", "3", "-File", str(folder), "-DisableRemediation"],
                              capture_output=True, text=True, timeout=900)
    except (OSError, subprocess.TimeoutExpired):
        return "couldn't run"
    if proc.returncode == 2 or "found" in (proc.stdout or "").lower() and "no threats" not in proc.stdout.lower():
        return "THREAT FOUND"
    return "no threats" if proc.returncode == 0 else f"exit {proc.returncode}"


def scan(folder: Path, defender=_defender) -> Report:
    folder = Path(folder)
    rep = Report()
    dangers = [(w, re.compile(p, re.I)) for w, p in DANGER]
    cautions = [(w, re.compile(p, re.I)) for w, p in CAUTION if p]
    for root, dirs, files in os.walk(folder):
        dirs[:] = [d for d in dirs if d != ".git"]
        for name in files:
            if rep.files >= MAX_FILES:
                rep.add("caution", "too many files to check them all", str(folder))
                break
            path = Path(root) / name
            rel = str(path.relative_to(folder))
            rep.files += 1
            ext = path.suffix.lower()
            if path.is_symlink():
                rep.add("caution", "a link pointing outside the folder", rel)
                continue
            if ext in BINARY_DANGER:
                rep.add("danger", f"ships a ready-made program ({ext})", rel)
                continue
            if ext in SCRIPT_CAUTION:
                rep.add("caution", f"includes a script that runs on its own ({ext})", rel)
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_BYTES or ext not in TEXT_EXT:
                continue
            try:
                text = path.read_text("utf-8", errors="replace")
            except OSError:
                continue
            for what, rx in dangers:
                if rx.search(text):
                    rep.add("danger", what, rel)
            for what, rx in cautions:
                if rx.search(text):
                    rep.add("caution", what, rel)
            if ext in (".py", ".js", ".ts", ".ps1") and not name.endswith((".min.js", ".bundle.js")) and \
                    any(len(line) > 3000 for line in text.splitlines()):
                rep.add("caution", "very long single lines (often hidden code)", rel)
            if name == "package.json":
                try:
                    scripts = json.loads(text).get("scripts", {}) or {}
                except ValueError:
                    scripts = {}
                for hook in ("preinstall", "install", "postinstall", "prepare"):
                    if hook in scripts:
                        level = "danger" if re.search(r"curl|wget|http|node\s+-e|powershell|base64", str(scripts[hook]),
                                                      re.I) else "caution"
                        rep.add(level, f"runs code when installed ({hook}: {str(scripts[hook])[:60]})", rel)
            if name == "setup.py" and re.search(r"\b(urlopen|requests\.(get|post)|subprocess|os\.system|socket)\b",
                                                text):
                rep.add("caution", "setup.py downloads or runs things while installing", rel)
    rep.defender = defender(folder)
    if rep.defender == "THREAT FOUND":
        rep.add("danger", "Microsoft Defender found a threat", str(folder.name))
    return rep


def check_and_keep(folder: Path, defender=_defender) -> Report:
    """Scan a freshly downloaded repo; delete it straight away if it's dangerous."""
    rep = scan(folder, defender)
    if rep.verdict == "danger":
        shutil.rmtree(folder, ignore_errors=True)
    return rep
