"""Allow-list for raw threading.Thread and asyncio.create_task usages (M5 Item 5.1.7).

Every raw thread or asyncio task in Prometheus must either be tracked
through `prometheus.jobs.runner.start_job` or explicitly justified here.
"""
from __future__ import annotations

# Map of relative POSIX path -> list of allowed reasons
# Map of relative POSIX path -> one reason per allowed raw call, in the order they appear in the file.
# A file may start only as many raw threads/tasks as it has reasons here (prometheus/jobs/lint.py).
ALLOWED_RAW_THREADS_AND_TASKS: dict[str, list[str]] = {
    "prometheus/jobs/runner.py": [
        "The tracked thread a job runs in",
        "The tracked asyncio task a job runs in",
        "The tracked thread an async job runs in when no event loop is running",
    ],
    "prometheus/desktop.py": [
        "The local web server behind the PyWebView window",
        "Debounce timer that saves the window position after it stops moving",
    ],
    "prometheus/hotkey.py": [
        "Low-level Windows keyboard hook listener (blocking Windows message loop)"
    ],
    "prometheus/tray.py": [
        "Native system tray icon message pump (pystray event loop)"
    ],
    "prometheus/actions/terminal.py": [
        "Reads the stdout pipe of a terminal command (part of that command's own run)",
        "Reads the stderr pipe of a terminal command (part of that command's own run)",
        "Waits for the terminal command to exit (awaited in the same call)",
        "Waits for a cancel request for the terminal command (awaited in the same call)",
    ],
    "prometheus/connectors/mcp_client.py": [
        "Subprocess JSON-RPC standard I/O reader thread waiting on synchronous MCP response with timeout"
    ],
    "prometheus/voice/loop.py": [
        "Native microphone audio stream loop (PyAudio / PortAudio hardware capture)",
        "Barge-in watcher that listens while Prometheus speaks (ends with the reply)",
    ],
    "prometheus/voice/brief.py": [
        "Small pool that fetches the parts of a spoken briefing at once; waited for before the call returns",
    ],
    "prometheus/photos.py": [
        "Idle timer that frees the photo models' memory after a quiet spell (8 GB laptop)",
    ],
    "prometheus/app.py": [
        "Sends one phone notification without holding up the caller (a few seconds, no state)",
    ],
    "prometheus/web/routes.py": [
        "App quit process termination delay (allows HTTP 200 response to flush before exit)",
        "Sends the 'notifications are on' test push without holding up the page",
    ],
}
