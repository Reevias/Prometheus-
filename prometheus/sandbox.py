"""Run a Python script safely, for sums, data and charts.

Two ways, picked automatically:
- Windows Sandbox (Windows Pro, when its feature is on): a throwaway Windows that disappears afterwards, with no
  network unless allowed. Slower to start (about 30 seconds) and uses about 1 GB of memory.
- Otherwise, a locked-down Python: a separate Python process in its own scratch folder with an audit hook that
  blocks the internet (unless allowed), starting programs, loading DLLs, and touching files outside the scratch
  folder. It gets no keys or settings (an empty environment), a 60-second limit and a memory limit.

The locked-down Python is strong against mistakes and ordinary scripts, not a wall against deliberately hostile
code, which is why every new script is shown to the owner and asked about first.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

TIME_LIMIT = 60
MEMORY_MB = 1024
OUTPUT_CHARS = 20_000
NEEDS_NET = "PROMETHEUS-SANDBOX-NEEDS-NETWORK"
GUARD_NAME = "_prometheus_guard.py"
SCRIPT_NAME = "script.py"

GUARD = r'''
import os, sys
SCRATCH = os.path.realpath(sys.argv[1])
NET = sys.argv[2] == "1"
SCRIPT = os.path.join(SCRATCH, "script.py")
READ_ROOTS = tuple({os.path.realpath(p) for p in (sys.prefix, sys.base_prefix, sys.exec_prefix, *sys.path) if p}
                   | {SCRATCH})
# private places: the owner's user folder (and other users'), and Prometheus's own data (keys, memory)
PRIVATE = tuple(os.path.realpath(p) for p in sys.argv[3].split(os.pathsep) if p)
NEEDS_NET = "PROMETHEUS-SANDBOX-NEEDS-NETWORK"
NET_EVENTS = {"socket.connect", "socket.bind", "socket.getaddrinfo", "socket.gethostbyname", "socket.gethostbyaddr",
              "socket.sendto", "socket.sendmsg", "urllib.Request", "http.client.connect", "ftplib.connect",
              "smtplib.connect", "imaplib.open", "poplib.connect", "telnetlib.Telnet.open", "webbrowser.open"}
PROC_PREFIXES = ("subprocess.", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.fork", "os.forkpty",
                 "os.startfile", "os.kill", "os.killpg", "_winapi.", "pty.spawn", "os.add_dll_directory")
CHANGE_EVENTS = {"os.remove", "os.rename", "os.rmdir", "os.mkdir", "os.chmod", "os.chown", "os.link", "os.symlink",
                 "os.truncate", "os.utime", "shutil.rmtree", "shutil.copyfile", "shutil.copymode", "shutil.copystat",
                 "shutil.move", "shutil.chown", "os.removexattr", "os.setxattr"}
LIST_EVENTS = {"os.listdir", "os.scandir", "glob.glob", "os.walk"}
WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC

def _inside(path, roots):
    try:
        p = os.path.realpath(os.fsdecode(path))
    except Exception:
        return False
    return any(p == r or p.startswith(r.rstrip(os.sep) + os.sep) for r in roots)

def _no(why):
    raise PermissionError("Prometheus sandbox: " + why)

def hook(event, args):
    if event in NET_EVENTS:
        if not NET:
            sys.__stderr__.write("\n" + NEEDS_NET + "\n")
            _no("this script has no internet. Ask Prometheus to run it with the internet if it needs it.")
        return
    if event.startswith(PROC_PREFIXES):
        _no("starting other programs isn't allowed.")
    if event == "ctypes.dlopen":
        if args and args[0] is not None and not _inside(args[0], READ_ROOTS):
            _no("loading DLLs isn't allowed.")
        return
    if event == "open":
        path, mode, flags = (list(args) + [None, None, None])[:3]
        if isinstance(path, int) or path is None:
            return
        writing = (isinstance(mode, str) and any(c in mode for c in "wax+")) or \
                  (mode is None and isinstance(flags, int) and flags & WRITE_FLAGS)
        if writing and not _inside(path, (SCRATCH,)):
            _no("it can only save files in its own folder.")
        if not writing and _inside(path, PRIVATE) and not _inside(path, READ_ROOTS):
            _no("it can only read files in its own folder.")
        return
    if event in CHANGE_EVENTS:
        for a in args:
            if isinstance(a, (str, bytes, os.PathLike)) and not _inside(a, (SCRATCH,)):
                _no("it can only change files in its own folder.")
        return
    if event in LIST_EVENTS:
        path = args[0] if args else "."
        if isinstance(path, (str, bytes, os.PathLike)) and _inside(path or ".", PRIVATE) \
                and not _inside(path or ".", READ_ROOTS):
            _no("it can only look inside its own folder.")

sys.argv = [SCRIPT]
sys.path.insert(0, SCRATCH)
os.chdir(SCRATCH)
sys.addaudithook(hook)
import runpy
runpy.run_path(SCRIPT, run_name="__main__")
'''


@dataclass
class RunResult:
    ok: bool
    output: str
    files: list[Path] = field(default_factory=list)
    needs_network: bool = False
    how: str = "locked-down Python"
    timed_out: bool = False

    def describe(self) -> str:
        head = ("It worked" if self.ok else "It stopped with an error") + f" (in the {self.how})."
        if self.timed_out:
            head = f"It took longer than {TIME_LIMIT} seconds, so I stopped it."
        files = ("\nFiles made: " + ", ".join(f.name for f in self.files)) if self.files else ""
        out = self.output.strip() or "(it printed nothing)"
        return f"{head}\n```\n{out}\n```{files}"


def windows_sandbox_available() -> bool:
    if os.name != "nt":
        return False
    return (Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsSandbox.exe").exists()


def new_folder(base: Path) -> Path:
    folder = Path(base) / time.strftime("run-%Y%m%d-%H%M%S")
    n = 1
    while folder.exists():
        n += 1
        folder = Path(base) / time.strftime(f"run-%Y%m%d-%H%M%S-{n}")
    folder.mkdir(parents=True)
    return folder


def _env(folder: Path) -> dict:
    """An almost empty environment: no API keys, tokens or settings reach the script."""
    env = {"PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1", "MPLBACKEND": "Agg",
           "HOME": str(folder), "USERPROFILE": str(folder), "TEMP": str(folder), "TMP": str(folder),
           "TMPDIR": str(folder), "MPLCONFIGDIR": str(folder)}
    for k in ("SYSTEMROOT", "SystemRoot", "WINDIR", "PATH", "LANG"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return env


def _posix_limits(memory_mb: int):
    def apply():
        import resource

        limit = memory_mb * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CPU, (TIME_LIMIT + 5, TIME_LIMIT + 5))
        resource.setrlimit(resource.RLIMIT_FSIZE, (200 * 1024 * 1024, 200 * 1024 * 1024))
    return apply


def _windows_job(proc: subprocess.Popen, memory_mb: int):
    """Put the process in a Job Object with a memory limit; it dies with the job. Best effort."""
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        job = k32.CreateJobObjectW(None, None)
        info = EXTENDED()
        # PROCESS_MEMORY | KILL_ON_JOB_CLOSE | ACTIVE_PROCESS (no child processes)
        info.Basic.LimitFlags = 0x100 | 0x2000 | 0x8
        info.Basic.ActiveProcessLimit = 1
        info.ProcessMemoryLimit = memory_mb * 1024 * 1024
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        k32.AssignProcessToJobObject(job, wintypes.HANDLE(int(proc._handle)))
        return job
    except Exception:
        return None


def _private_places(folder: Path) -> list[str]:
    from prometheus.config import data_dir

    home = Path.home().resolve()
    places = [str(home), str(data_dir().resolve())]
    users = home.parent
    if users != Path(users.anchor) and users.name.lower() in ("users", "home"):
        places.append(str(users))                       # other people's folders on this laptop too
    for root in ("/root", os.environ.get("PUBLIC", "")):
        if root and Path(root).exists():
            places.append(root)
    return places


def _new_files(folder: Path) -> list[Path]:
    skip = {GUARD_NAME, SCRIPT_NAME, "stdout.txt", "done.txt", "run.wsb", "run.cmd"}
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.name not in skip
                  and "__pycache__" not in p.parts and not p.name.startswith((".", "fontlist-"))
                  and p.parent.name not in ("matplotlib", ".cache"))


def run_locked(code: str, folder: Path, network: bool = False, timeout: int = TIME_LIMIT,
               memory_mb: int = MEMORY_MB, python: str = "") -> RunResult:
    folder = Path(folder).resolve()
    (folder / SCRIPT_NAME).write_text(code, encoding="utf-8")
    (folder / GUARD_NAME).write_text(GUARD, encoding="utf-8")
    cmd = [python or sys.executable, "-I", str(folder / GUARD_NAME), str(folder), "1" if network else "0",
           os.pathsep.join(_private_places(folder))]
    kw = {"cwd": str(folder), "env": _env(folder), "stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
          "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kw["creationflags"] = 0x08000000          # CREATE_NO_WINDOW
    else:
        kw["preexec_fn"] = _posix_limits(memory_mb)
    proc = subprocess.Popen(cmd, **kw)
    job = _windows_job(proc, memory_mb) if os.name == "nt" else None
    timed_out = False
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        out, _ = proc.communicate()
        timed_out = True
    finally:
        del job
    text = out.decode("utf-8", "replace")
    needs = NEEDS_NET in text
    text = text.replace(NEEDS_NET, "").replace(str(folder / GUARD_NAME), "sandbox")
    text = _tidy_traceback(text)
    if len(text) > OUTPUT_CHARS:
        text = text[:OUTPUT_CHARS] + "\n... (cut short)"
    return RunResult(proc.returncode == 0 and not timed_out, text, _new_files(folder), needs and not network,
                     timed_out=timed_out)


def _tidy_traceback(text: str) -> str:
    """Hide the sandbox's own lines from error messages so the owner sees only their script."""
    lines = text.splitlines()
    out, skip = [], False
    for line in lines:
        if line.startswith('  File "') and ("runpy" in line or "sandbox" in line or "<frozen runpy>" in line):
            skip = True
            continue
        if skip and line.startswith("    "):
            continue
        skip = False
        out.append(line)
    return "\n".join(out)


def run_windows_sandbox(code: str, folder: Path, network: bool = False, timeout: int = 240) -> RunResult:
    """Run in Windows Sandbox: this Python's folder is shared read-only, the run folder read-write. The sandbox
    shuts itself down when the script ends. Raises OSError if Windows Sandbox can't start (the caller falls back)."""
    folder = Path(folder).resolve()
    (folder / SCRIPT_NAME).write_text(code, encoding="utf-8")
    py_home = Path(sys.base_prefix)
    cmd = (f'cd /d C:\\work && C:\\py\\python.exe -I script.py > stdout.txt 2>&1 & '
           f'echo %errorlevel% > done.txt & shutdown /s /t 0')
    (folder / "run.cmd").write_text(cmd, encoding="utf-8")
    wsb = f"""<Configuration>
  <Networking>{'Enable' if network else 'Disable'}</Networking>
  <VGpu>Disable</VGpu><AudioInput>Disable</AudioInput><VideoInput>Disable</VideoInput>
  <ClipboardRedirection>Disable</ClipboardRedirection><PrinterRedirection>Disable</PrinterRedirection>
  <MemoryInMB>2048</MemoryInMB>
  <MappedFolders>
    <MappedFolder><HostFolder>{py_home}</HostFolder><SandboxFolder>C:\\py</SandboxFolder><ReadOnly>true</ReadOnly></MappedFolder>
    <MappedFolder><HostFolder>{folder}</HostFolder><SandboxFolder>C:\\work</SandboxFolder><ReadOnly>false</ReadOnly></MappedFolder>
  </MappedFolders>
  <LogonCommand><Command>cmd /c C:\\work\\run.cmd</Command></LogonCommand>
</Configuration>"""
    (folder / "run.wsb").write_text(wsb, encoding="utf-8")
    exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
    subprocess.Popen([str(exe), str(folder / "run.wsb")])
    end = time.time() + timeout
    while time.time() < end and not (folder / "done.txt").exists():
        time.sleep(1)
    if not (folder / "done.txt").exists():
        raise OSError("Windows Sandbox didn't finish in time.")
    text = (folder / "stdout.txt").read_text("utf-8", "replace") if (folder / "stdout.txt").exists() else ""
    code_ = (folder / "done.txt").read_text().strip()
    needs = not network and any(w in text for w in ("getaddrinfo failed", "NameResolutionError",
                                                    "Failed to establish a new connection", "urlopen error"))
    return RunResult(code_ == "0", text[:OUTPUT_CHARS], _new_files(folder), needs, how="Windows Sandbox")


def run(code: str, base: Path, network: bool = False, use_windows_sandbox: bool = True,
        files: list[Path] | None = None, extra: dict[str, str] | None = None) -> tuple[RunResult, Path]:
    """extra: small text files to put next to the script (a tool's inputs.json)."""
    folder = new_folder(base)
    for f in files or []:
        shutil.copy2(f, folder / Path(f).name)
    for name, text in (extra or {}).items():
        (folder / Path(name).name).write_text(text, encoding="utf-8")
    given = {Path(f).name for f in files or []} | {Path(n).name for n in extra or {}}
    result = None
    if use_windows_sandbox and windows_sandbox_available():
        try:
            result = run_windows_sandbox(code, folder, network)
        except OSError:
            result = None                          # fall back to the locked-down Python
    result = result or run_locked(code, folder, network)
    result.files = [f for f in result.files if f.name not in given]
    return result, folder
