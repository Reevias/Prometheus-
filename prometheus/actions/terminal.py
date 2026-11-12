"""Persistent, asynchronous streaming terminal execution with process-tree cleanup."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from prometheus.safety.paths import PathBlocked, PathPolicy
from prometheus.safety.secrets import PATTERNS, KEY_NAMES, get_secret

MAX_TERMINAL_OUTPUT_CHARS = 50_000
DEFAULT_TIMEOUT = 30.0
MAX_TIMEOUT = 300.0


@dataclass
class TerminalResult:
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    cancelled: bool = False
    duration: float = 0.0


def scrub_secrets(text: str) -> str:
    """Scrub sensitive API keys, tokens, and credentials from terminal output."""
    if not text:
        return ""
    import re
    result = text
    for label, rx_pattern in PATTERNS.items():
        rx = re.compile(rx_pattern)
        result = rx.sub(f"[REDACTED_{label.upper().replace(' ', '_')}]", result)
    try:
        saved = [get_secret(name) for name in KEY_NAMES]
        for val in saved:
            if val and len(val) >= 12:
                result = result.replace(val, "[REDACTED_SAVED_SECRET]")
    except Exception:
        pass
    return result


SAFE_ENV_ALLOWLIST = frozenset({
    # Windows system essentials
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATH", "PATHEXT",
    "TEMP", "TMP", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "APPDATA", "LOCALAPPDATA", "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE", "PROCESSOR_IDENTIFIER", "PROCESSOR_LEVEL",
    "OS",
    # Unix system essentials
    "HOME", "USER", "SHELL", "LANG", "LC_ALL", "TMPDIR", "TERM",
    # Python environment essentials
    "VIRTUAL_ENV",
})

BLOCKED_SECRET_SUBSTRINGS = (
    "KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH", "CREDENTIAL", "PRIVATE"
)


def build_isolated_environment(env_overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Construct a clean, minimal environment isolated from parent credentials and API keys.

    Only permits explicit system essentials from SAFE_ENV_ALLOWLIST.
    Explicitly filters out all credential and secret variables present in the parent
    environment (e.g. ANTHROPIC_API_KEY, OPENAI_API_KEY, GITHUB_TOKEN).
    Deliberate non-secret overrides can be passed via env_overrides.
    """
    clean_env: dict[str, str] = {}
    for k, v in os.environ.items():
        k_upper = k.upper()
        if k_upper in SAFE_ENV_ALLOWLIST:
            if any(sub in k_upper for sub in BLOCKED_SECRET_SUBSTRINGS):
                continue
            clean_env[k] = v

    if env_overrides:
        clean_env.update(env_overrides)

    return clean_env


def redact_command(argv: list[str]) -> list[str]:
    """Return a diagnostic copy of argv with sensitive tokens/credentials scrubbed."""
    return [scrub_secrets(arg) for arg in argv]


class WinJobManager:
    """Encapsulates Windows Job Object lifecycle for descendant process cleanup.

    Ensures that when the job object is closed, all spawned child and grandchild
    processes are immediately and unconditionally terminated by the Windows kernel.
    """

    def __init__(self):
        self.job_handle = None
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes
            k32 = ctypes.windll.kernel32
            job = k32.CreateJobObjectW(None, None)
            if job:
                class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("PerProcessUserTimeLimit", ctypes.c_int64),
                        ("PerJobUserTimeLimit", ctypes.c_int64),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD),
                    ]
                class IO_COUNTERS(ctypes.Structure):
                    _fields_ = [("r_op", ctypes.c_uint64), ("w_op", ctypes.c_uint64), ("o_op", ctypes.c_uint64),
                                ("r_xfer", ctypes.c_uint64), ("w_xfer", ctypes.c_uint64), ("o_xfer", ctypes.c_uint64)]
                class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                    _fields_ = [
                        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                        ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryLimit", ctypes.c_size_t),
                        ("PeakJobMemoryLimit", ctypes.c_size_t),
                    ]
                info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                JobObjectExtendedLimitInformation = 9
                res = k32.SetInformationJobObject(
                    job,
                    JobObjectExtendedLimitInformation,
                    ctypes.byref(info),
                    ctypes.sizeof(info),
                )
                if res:
                    self.job_handle = job
                else:
                    k32.CloseHandle(job)

    def assign_process(self, pid: int) -> bool:
        """Assign target process to Win32 Job Object."""
        if not self.job_handle or sys.platform != "win32":
            return False
        import ctypes
        k32 = ctypes.windll.kernel32
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        h_proc = k32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if not h_proc:
            return False
        try:
            return bool(k32.AssignProcessToJobObject(self.job_handle, h_proc))
        finally:
            k32.CloseHandle(h_proc)

    def close(self):
        """Close job object handle, triggering kernel cleanup of all descendant processes."""
        if self.job_handle and sys.platform == "win32":
            import ctypes
            ctypes.windll.kernel32.CloseHandle(self.job_handle)
            self.job_handle = None


def resume_process_threads(pid: int) -> None:
    """Resume suspended threads of process so it begins execution inside Job Object."""
    if sys.platform != "win32":
        return
    import ctypes
    k32 = ctypes.windll.kernel32
    TH32CS_SNAPTHREAD = 0x00000004
    THREAD_SUSPEND_RESUME = 0x0002

    class THREADENTRY32(ctypes.Structure):
        _fields_ = [
            ("dwSize", ctypes.c_ulong),
            ("cntUsage", ctypes.c_ulong),
            ("th32ThreadID", ctypes.c_ulong),
            ("th32OwnerProcessID", ctypes.c_ulong),
            ("tpBasePri", ctypes.c_long),
            ("tpDeltaPri", ctypes.c_long),
            ("dwFlags", ctypes.c_ulong),
        ]

    h_snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if not h_snap or h_snap == -1:
        raise OSError("CreateToolhelp32Snapshot failed to inspect process threads")
    try:
        te = THREADENTRY32()
        te.dwSize = ctypes.sizeof(THREADENTRY32)
        if k32.Thread32First(h_snap, ctypes.byref(te)):
            while True:
                if te.th32OwnerProcessID == pid:
                    h_thread = k32.OpenThread(THREAD_SUSPEND_RESUME, False, te.th32ThreadID)
                    if h_thread:
                        try:
                            k32.ResumeThread(h_thread)
                        finally:
                            k32.CloseHandle(h_thread)
                if not k32.Thread32Next(h_snap, ctypes.byref(te)):
                    break
    finally:
        k32.CloseHandle(h_snap)


def kill_process_tree(pid: int) -> None:
    """Reliably terminate a process and all its descendants."""
    if sys.platform == "win32":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
        except Exception:
            pass
    else:
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except Exception:
            try:
                os.kill(pid, signal.SIGKILL)
            except Exception:
                pass


class AsyncTerminal:
    """Asynchronous, streaming terminal execution engine with process tree confinement."""

    def __init__(self, policy: PathPolicy):
        self.policy = policy

    async def execute(
        self,
        argv: list[str],
        cwd: Path | str,
        timeout: float = DEFAULT_TIMEOUT,
        env_overrides: dict[str, str] | None = None,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        cancel_event: asyncio.Event | None = None,
        max_output_chars: int = MAX_TERMINAL_OUTPUT_CHARS,
    ) -> TerminalResult:
        """Execute argv with shell=False, streaming output, timeout, and Job Object cleanup."""
        if not argv:
            raise ValueError("argv cannot be empty")

        # Validate cwd via PathPolicy
        canonical_cwd = self.policy.check_read(cwd)
        if not canonical_cwd.exists() or not canonical_cwd.is_dir():
            raise FileNotFoundError(f"Working directory does not exist or is not a folder: {cwd}")

        timeout = min(max(0.1, timeout), MAX_TIMEOUT)

        env = build_isolated_environment(env_overrides=env_overrides)

        creationflags = 0
        if sys.platform == "win32":
            CREATE_SUSPENDED = 0x00000004
            creationflags = subprocess.CREATE_NO_WINDOW | CREATE_SUSPENDED

        start_time = time.time()
        job = WinJobManager()

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(canonical_cwd),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            creationflags=creationflags,
        )

        if sys.platform == "win32":
            assigned = job.assign_process(proc.pid)
            if not assigned:
                try:
                    proc.kill()
                except Exception:
                    pass
                job.close()
                raise RuntimeError(
                    f"Security failure: Failed to assign process {proc.pid} to Job Object before execution"
                )
            resume_process_threads(proc.pid)

        stdout_buf: list[str] = []
        stderr_buf: list[str] = []
        stdout_chars = 0
        stderr_chars = 0
        stdout_truncated = False
        stderr_truncated = False

        STREAM_CHUNK_SIZE = 8192

        async def read_stream(stream: asyncio.StreamReader, is_stdout: bool):
            nonlocal stdout_chars, stderr_chars, stdout_truncated, stderr_truncated
            callback = on_stdout if is_stdout else on_stderr
            line_buffer = ""

            def handle_token(text: str):
                nonlocal stdout_chars, stderr_chars, stdout_truncated, stderr_truncated
                scrubbed = scrub_secrets(text)
                if callback:
                    try:
                        callback(scrubbed)
                    except Exception:
                        pass

                if is_stdout:
                    if stdout_chars < max_output_chars:
                        if stdout_chars + len(scrubbed) > max_output_chars:
                            remaining = max_output_chars - stdout_chars
                            stdout_buf.append(scrubbed[:remaining])
                            stdout_buf.append(f"\n[Output truncated at {max_output_chars} characters]")
                            stdout_chars = max_output_chars
                            stdout_truncated = True
                        else:
                            stdout_buf.append(scrubbed)
                            stdout_chars += len(scrubbed)
                    elif not stdout_truncated:
                        stdout_buf.append(f"\n[Output truncated at {max_output_chars} characters]")
                        stdout_truncated = True
                else:
                    if stderr_chars < max_output_chars:
                        if stderr_chars + len(scrubbed) > max_output_chars:
                            remaining = max_output_chars - stderr_chars
                            stderr_buf.append(scrubbed[:remaining])
                            stderr_buf.append(f"\n[Output truncated at {max_output_chars} characters]")
                            stderr_chars = max_output_chars
                            stderr_truncated = True
                        else:
                            stderr_buf.append(scrubbed)
                            stderr_chars += len(scrubbed)
                    elif not stderr_truncated:
                        stderr_buf.append(f"\n[Output truncated at {max_output_chars} characters]")
                        stderr_truncated = True

            while True:
                # Bounded chunked stream reads (prevents memory exhaustion on newline-free output)
                chunk = await stream.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                decoded = chunk.decode("utf-8", errors="replace")
                line_buffer += decoded
                while "\n" in line_buffer:
                    line, line_buffer = line_buffer.split("\n", 1)
                    handle_token(line + "\n")
                if len(line_buffer) >= STREAM_CHUNK_SIZE:
                    handle_token(line_buffer[:STREAM_CHUNK_SIZE])
                    line_buffer = line_buffer[STREAM_CHUNK_SIZE:]

            if line_buffer:
                handle_token(line_buffer)
                line_buffer = ""

        reader_task_out = asyncio.create_task(read_stream(proc.stdout, True))
        reader_task_err = asyncio.create_task(read_stream(proc.stderr, False))

        timed_out = False
        cancelled = False

        async def wait_process():
            await proc.wait()
            await asyncio.gather(reader_task_out, reader_task_err, return_exceptions=True)

        async def wait_cancel():
            if cancel_event is not None:
                await cancel_event.wait()

        tasks = [asyncio.create_task(wait_process())]
        if cancel_event is not None:
            tasks.append(asyncio.create_task(wait_cancel()))

        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()

            if not done:
                timed_out = True
                kill_process_tree(proc.pid)
                job.close()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
            elif cancel_event is not None and cancel_event.is_set():
                cancelled = True
                kill_process_tree(proc.pid)
                job.close()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass

        finally:
            job.close()
            reader_task_out.cancel()
            reader_task_err.cancel()

        exit_code = proc.returncode if proc.returncode is not None else -1
        duration = time.time() - start_time

        return TerminalResult(
            command=redact_command(argv),
            exit_code=exit_code,
            stdout="".join(stdout_buf),
            stderr="".join(stderr_buf),
            timed_out=timed_out,
            cancelled=cancelled,
            duration=duration,
        )
