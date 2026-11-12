"""Unit and integration tests for Prometheus 3.0 AsyncTerminal engine (Milestone M1 Phase 4)."""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from prometheus.actions.terminal import (
    AsyncTerminal,
    TerminalResult,
    kill_process_tree,
    scrub_secrets,
)
from prometheus.safety.paths import PathBlocked, PathPolicy


@pytest.fixture
def terminal_env(tmp_path):
    home = tmp_path / "home"
    data = home / "AppData" / "Local" / "Prometheus"
    code = home / "code" / "prometheus"
    project = home / "projects" / "my_app"
    for d in (home / "Downloads", home / ".ssh", data, code, project):
        d.mkdir(parents=True, exist_ok=True)
    policy = PathPolicy(home=home, data_dir=data, code_dir=code)
    terminal = AsyncTerminal(policy=policy)
    return home, project, data, policy, terminal


# ============================================================================
# Basic Execution and Streaming Output
# ============================================================================

def test_terminal_exec_simple(terminal_env):
    _, project, _, _, terminal = terminal_env

    async def run():
        return await terminal.execute(
            [sys.executable, "-c", "print('hello from terminal')"],
            cwd=project,
        )

    res = asyncio.run(run())
    assert res.exit_code == 0
    assert "hello from terminal" in res.stdout
    assert res.stderr == ""
    assert res.timed_out is False
    assert res.cancelled is False
    assert res.duration > 0.0


def test_terminal_streaming(terminal_env):
    _, project, _, _, terminal = terminal_env
    captured_lines = []

    def on_out(line: str):
        captured_lines.append(line.strip())

    async def run():
        code = "import sys, time\nfor i in range(3):\n    print(f'chunk_{i}', flush=True)\n"
        return await terminal.execute(
            [sys.executable, "-c", code],
            cwd=project,
            on_stdout=on_out,
        )

    res = asyncio.run(run())
    assert res.exit_code == 0
    assert "chunk_0" in captured_lines
    assert "chunk_1" in captured_lines
    assert "chunk_2" in captured_lines


# ============================================================================
# Timeouts, Cancellation, and Process Tree Confinement
# ============================================================================

def test_terminal_timeout(terminal_env):
    _, project, _, _, terminal = terminal_env

    async def run():
        return await terminal.execute(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=project,
            timeout=0.5,
        )

    res = asyncio.run(run())
    assert res.timed_out is True
    assert res.exit_code != 0


def test_terminal_cancel(terminal_env):
    _, project, _, _, terminal = terminal_env

    async def run():
        cancel_evt = asyncio.Event()

        async def trigger_cancel():
            await asyncio.sleep(0.3)
            cancel_evt.set()

        asyncio.create_task(trigger_cancel())

        return await terminal.execute(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            cwd=project,
            cancel_event=cancel_evt,
            timeout=10.0,
        )

    res = asyncio.run(run())
    assert res.cancelled is True
    assert res.exit_code != 0


def test_terminal_child_kill(terminal_env):
    """Ensure descendant child and grandchild processes are terminated on cancel."""
    _, project, _, _, terminal = terminal_env
    child_script = project / "spawn_children.py"
    child_script.write_text(
        "import subprocess, sys, time\n"
        "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print('spawned', p.pid, flush=True)\n"
        "time.sleep(30)\n"
    )

    async def run():
        cancel_evt = asyncio.Event()

        async def trigger_cancel():
            await asyncio.sleep(0.5)
            cancel_evt.set()

        asyncio.create_task(trigger_cancel())

        return await terminal.execute(
            [sys.executable, str(child_script)],
            cwd=project,
            cancel_event=cancel_evt,
            timeout=10.0,
        )

    res = asyncio.run(run())
    assert res.cancelled is True


# ============================================================================
# Buffer Bounds and Secret Scrubbing
# ============================================================================

def test_terminal_output_cap(terminal_env):
    _, project, _, _, terminal = terminal_env

    async def run():
        code = "print('A' * 20000)"
        return await terminal.execute(
            [sys.executable, "-c", code],
            cwd=project,
            max_output_chars=2000,
        )

    res = asyncio.run(run())
    assert len(res.stdout) <= 2200
    assert "[Output truncated at 2000 characters]" in res.stdout


def test_terminal_secret_scrub(terminal_env):
    _, project, _, _, terminal = terminal_env
    fake_key = "sk-ant-api03-1234567890abcdef1234567890"

    async def run():
        code = f"print('My key is {fake_key}')"
        return await terminal.execute(
            [sys.executable, "-c", code],
            cwd=project,
        )

    res = asyncio.run(run())
    assert fake_key not in res.stdout
    assert "[REDACTED_ANTHROPIC_KEY]" in res.stdout


# ============================================================================
# Security, Path Containment, and Injection Defense
# ============================================================================

def test_terminal_unauth_cwd(terminal_env):
    home, _, _, _, terminal = terminal_env
    blocked_dir = home / ".ssh"

    async def run():
        await terminal.execute([sys.executable, "-c", "print(1)"], cwd=blocked_dir)

    with pytest.raises(PathBlocked):
        asyncio.run(run())


def test_terminal_shell_false_injection(terminal_env):
    """shell=False treats metacharacters literally, preventing command injection."""
    _, project, _, _, terminal = terminal_env

    async def run():
        return await terminal.execute(
            [sys.executable, "-c", "import sys; print(sys.argv[1])", "safe & echo injected"],
            cwd=project,
        )

    res = asyncio.run(run())
    assert res.exit_code == 0
    assert "safe & echo injected" in res.stdout
    assert "injected\n" not in res.stdout.splitlines()


def test_terminal_multiple_workers_allowed(terminal_env):
    """Verify Job Object does not restrict worker subprocesses (ActiveProcessLimit != 1)."""
    _, project, _, _, terminal = terminal_env

    worker_file = project / "worker_test.py"
    worker_file.write_text(
        "import concurrent.futures\n\n"
        "def work(x):\n"
        "    return x * 2\n\n"
        "if __name__ == '__main__':\n"
        "    with concurrent.futures.ProcessPoolExecutor(max_workers=2) as ex:\n"
        "        results = list(ex.map(work, [1, 2, 3]))\n"
        "    print('Results:', results)\n",
        encoding="utf-8",
    )

    async def run():
        return await terminal.execute(
            [sys.executable, str(worker_file)],
            cwd=project,
            timeout=15.0,
        )

    res = asyncio.run(run())
    assert res.exit_code == 0
    assert "Results: [2, 4, 6]" in res.stdout


# ============================================================================
# Findings C & D: Secret Isolation & Command Redaction Tests
# ============================================================================

def test_terminal_env_secret_isolation(terminal_env, monkeypatch):
    """Parent API keys and secrets must NOT be inherited by child processes."""
    home, project, _, _, terminal = terminal_env
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-supersecret1234567890")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-supersecret1234567890")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_supersecret1234567890")
    monkeypatch.setenv("MY_APP_SECRET", "very_secret_value_12345")

    async def run():
        code = (
            "import os, sys\n"
            "for k in os.environ:\n"
            "    print(f'ENV:{k}')\n"
        )
        return await terminal.execute([sys.executable, "-c", code], cwd=project)

    res = asyncio.run(run())
    assert res.exit_code == 0
    lines = res.stdout.splitlines()
    env_keys = [line.split("ENV:")[1] for line in lines if line.startswith("ENV:")]

    assert "ANTHROPIC_API_KEY" not in env_keys
    assert "OPENAI_API_KEY" not in env_keys
    assert "GITHUB_TOKEN" not in env_keys
    assert "MY_APP_SECRET" not in env_keys


def test_terminal_command_argument_redaction(terminal_env):
    """Returned TerminalResult.command must have sensitive tokens scrubbed."""
    home, project, _, _, terminal = terminal_env
    fake_key = "sk-ant-api03-abcdef1234567890abcdef1234567890"

    async def run():
        return await terminal.execute(
            [sys.executable, "-c", "import sys; print('ARG:', sys.argv[1])", fake_key],
            cwd=project,
        )

    res = asyncio.run(run())
    assert res.exit_code == 0
    assert "[REDACTED_ANTHROPIC_KEY]" in res.stdout
    assert fake_key not in res.command
    assert any("[REDACTED_ANTHROPIC_KEY]" in arg for arg in res.command)


# ============================================================================
# Finding E: Pre-Execution Job Object Containment Tests
# ============================================================================

def test_terminal_job_assignment_failure_aborts_without_execution(terminal_env, monkeypatch):
    """If Job Object assignment fails, process must be killed without running user code."""
    home, project, _, _, terminal = terminal_env
    canary_file = project / "canary.txt"

    # Mock assign_process to fail
    import prometheus.actions.terminal as term_mod
    monkeypatch.setattr(term_mod.WinJobManager, "assign_process", lambda self, pid: False)

    code = f"with open(r'{canary_file}', 'w') as f: f.write('executed')\n"

    async def run():
        return await terminal.execute([sys.executable, "-c", code], cwd=project)

    if sys.platform == "win32":
        with pytest.raises(RuntimeError, match="Security failure: Failed to assign process"):
            asyncio.run(run())
        # The process was killed before resuming, so canary file must NOT exist!
        assert not canary_file.exists()


# ============================================================================
# Finding F: Bounded Stream Reading Tests (Newline-Free Output)
# ============================================================================

def test_terminal_huge_newline_free_stdout(terminal_env):
    """Verify 200,000 newline-free bytes on stdout do not exhaust memory or deadlock."""
    home, project, _, _, terminal = terminal_env

    async def run():
        # Emit 200k characters with no newline
        code = "import sys; sys.stdout.write('A' * 200_000); sys.stdout.flush()"
        return await terminal.execute([sys.executable, "-c", code], cwd=project, timeout=15.0)

    res = asyncio.run(run())
    assert res.exit_code == 0
    assert len(res.stdout) <= 50_000 + len("\n[Output truncated at 50000 characters]")
    assert "[Output truncated at 50000 characters]" in res.stdout


def test_terminal_huge_newline_free_stderr(terminal_env):
    """Verify 200,000 newline-free bytes on stderr do not exhaust memory or deadlock."""
    home, project, _, _, terminal = terminal_env

    async def run():
        # Emit 200k characters with no newline on stderr
        code = "import sys; sys.stderr.write('E' * 200_000); sys.stderr.flush()"
        return await terminal.execute([sys.executable, "-c", code], cwd=project, timeout=15.0)

    res = asyncio.run(run())
    assert res.exit_code == 0
    assert len(res.stderr) <= 50_000 + len("\n[Output truncated at 50000 characters]")
    assert "[Output truncated at 50000 characters]" in res.stderr
