"""Allow-list for raw threading.Thread and asyncio.create_task usages (M5 Item 5.1.7).

Every raw thread or asyncio task in Prometheus must either be tracked
through `prometheus.jobs.runner.start_job` or explicitly justified here.
"""
from __future__ import annotations

# Map of relative POSIX path -> list of allowed reasons
ALLOWED_RAW_THREADS_AND_TASKS: dict[str, list[str]] = {
    "prometheus/jobs/runner.py": [
        "Internal job runner implementation (spawns the tracked threads and asyncio tasks)"
    ],
    "prometheus/desktop.py": [
        "PyWebView native GUI window event loop and OS window message pump"
    ],
    "prometheus/hotkey.py": [
        "Low-level Windows keyboard hook listener (blocking Windows message loop)"
    ],
    "prometheus/tray.py": [
        "Native system tray icon message pump (pystray event loop)"
    ],
    "prometheus/actions/terminal.py": [
        "Subprocess standard I/O streaming pipes (internal async byte readers for stdout/stderr)"
    ],
    "prometheus/connectors/mcp_client.py": [
        "Subprocess JSON-RPC standard I/O reader thread waiting on synchronous MCP response with timeout"
    ],
    "prometheus/voice/loop.py": [
        "Native microphone audio stream loop (PyAudio / PortAudio hardware capture)"
    ],
    "prometheus/connectors/browser.py": [
        "moves in round 2"
    ],
    "prometheus/web/routes.py": [
        "App quit process termination delay (allows HTTP 200 response to flush before exit)"
    ],
}
