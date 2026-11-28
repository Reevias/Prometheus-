# M6 Laptop Check (`prometheus-v3`)

Checked on the owner's Windows laptop from this clone (`C:\Users\saive\prometheus-m5`, branch `prometheus-v3`, commit `d3b4cbb`) without changing any code.

## Environment

- **OS:** Windows 11 Home Single Language (24H2, x86_64)
- **Hardware:** Vivobook_ASUSLaptop X1504ZA, 12th Gen Intel Core i3-1215U, 7.69 GiB RAM
- **Python:** `C:\Users\saive\Prometheus-\.venv\Scripts\python.exe` (Python 3.14)
- **Desktop state during live checks:** Windows screen was locked (`Logon User Interface`, `GetForegroundWindow() == 0`)
- **Phone USB state during live checks:** `adb devices -l` was empty (`List of devices attached` had no devices) when the live `mobile-mcp` check ran (`CPH2707` / `3C15BH001TW00000` had been attached in an earlier session, then disconnected before the check)
- **Network adapters:** `Wi-Fi` (`MediaTek Wi-Fi 6E MT7902 Wireless LAN Card`) was the only `Up` adapter (`Bluetooth Network Connection` was `Disconnected`; no Ethernet)

## Test Suite (`python -m pytest -q`)

Command run from `C:\Users\saive\prometheus-m5`:

- **Uninterrupted run (`d3b4cbb`):** `2177 passed, 49 skipped, 4 warnings in 2291.37s (0:38:11)` (`0` failed, `0` errors)
- *Note:* An earlier run was interrupted mid-way when Windows went to sleep during `test_m5_jobs_browser.py` (`net::ERR_NETWORK_IO_SUSPENDED` in Chromium, resulting in `20 failed, 2074 passed, 45 skipped, 5 warnings, 35 errors`). Re-running without sleep passed cleanly.

## Live Checks (from this clone)

Server started with `PYTHONPATH=C:\Users\saive\prometheus-m5` via `python -m prometheus.cli serve --host 127.0.0.1 --port 8065` against a fresh `PROMETHEUS_HOME` temp folder (`prometheus.__file__` resolved to `C:\Users\saive\prometheus-m5\prometheus\__init__.py`; `GET /healthz` returned `200 {"app": "prometheus"}`).

| Check | Status | What was observed |
| --- | --- | --- |
| Click & type in Notepad (each asks first) | **Failed / Partial** | Asking first worked, but physical click and type failed because Windows was on the lock screen (`GetForegroundWindow() == 0`). Also, `pc_control` was missing from `GET /v3/agents` on the live server. |
| Stop all mid-action | **Passed** | Active UIA PowerShell subprocess (`PID 4228`) aborted in `0.04 s` with `StoppedError` and `0` leftover processes; `POST /kill` and `POST /kill/reset` returned `200`. |
| Phone tap & screen read via `mobile-mcp` | **Failed** | `POST /settings/phone/mobile-mcp` returned `200` and `mobile-mcp` queried devices, but returned `PhoneError: The phone isn't connected right now...` (`adb devices -l` was empty). |
| Control laptop from phone app | **Partial (API passed; phone app UI not tested)** | Pairing (`200`), 19 actions listed, `apps_running` (`done`), and `click` (`waiting_laptop` + visible on `/v3/approvals`) all worked via `/api/remote/*`; physical Android UI was not tapped because phone was disconnected. |
| Turn off Wi-Fi & use Prometheus offline | **Partial (physical Wi-Fi not disabled; offline flow passed)** | Physical Wi-Fi could not be turned off because it is the only active network adapter for this session; local server (`POST /chats` -> `303`, `GET /v3/memory` -> `200`) and encrypted outbox queueing during simulated `Offline` flush worked (`waiting: 1`, `sent: 0`). |
| Move small task to Cloudroom (asks first) | **Partial (passed against local Cloudroom server; no Render URL set)** | Default settings had empty `cloud_url` / `cloudroom_url`; against a local `cloudroom_server` from this clone, unapproved move was refused, approval showed code/limits/keys notice, and approving ran `print(2 + 3)` to `done` with output `"5"`. |

### Details

1. **Click and type in Notepad (each asks first) — Failed / Partial**
   - Running from outside the clone without `PYTHONPATH` fell back to the installed copy (`C:\Users\saive\Prometheus-`), which raised `ModuleNotFoundError: No module named 'prometheus.pc_control'`. Setting `PYTHONPATH=C:\Users\saive\prometheus-m5` imported the clone.
   - `notepad.exe` launched (`apps_running` saw `Untitled - Notepad`).
   - `PCControlAgent` paused and asked before both actions (`status == "waiting_approval"`):
     - `Approval #1 required: Click left (1x) at (300, 300) on the PC screen`
     - `Approval #2 required: Type text (16 chars) into active window: 'hello prometheus'`
   - Resolving both approvals returned `"Failed: Desktop has no active foreground window (screen may be locked)."` because Windows was locked (`GetForegroundWindow()` returned `0`).
   - Bug noticed on the live server: `GET /v3/agents` did not contain `pc_control` (`pc_control_on_agents_page: false`). `prometheus/agents/__init__.py` does not import `prometheus.pc_control.agent` before `prometheus/app.py` copies `dict(REGISTRY)` into `Orchestrator` (only `prometheus/orchestrator/registry.py` imports it later), although `_pc_approvals` is registered on `services.approvals`.

2. **Stop all mid-action — Passed**
   - Started a slow `run_ps_abortable("Start-Sleep -Seconds 15", ...)` UIA PowerShell child process (`PID 4228`) and triggered `KillSwitch.trigger("Stop all mid-action")`.
   - Aborted in `0.04 s` with `StoppedError: Stopped while reading UI controls.`, and `psutil` confirmed `0` leftover PowerShell processes.
   - Live HTTP `POST /kill` and `POST /kill/reset` both returned `200`.

3. **Phone tap and screen read through `mobile-mcp` — Failed**
   - `POST /settings/phone/mobile-mcp` returned `200`.
   - `MobilePhone` connected through `ConnectionManager` to `mobile-mcp` and called `mobile_list_available_devices`, which failed with:
     `PhoneError: The phone isn't connected right now. Plug in USB or enable Wireless debugging and connect mobile-mcp.`
   - Verified with `adb devices -l`, which returned an empty device list at check time, so phone tap and screen read could not execute on a physical phone.

4. **Control the laptop from the phone app — Partial**
   - Tested the laptop HTTP endpoints (`/api/remote/*`): enabled `remote_control` (`200`), generated a pairing code (`POST /api/v3/remote/pairing-code` -> `200`), paired (`POST /api/remote/pair` -> `200`), and fetched `19` actions (`GET /api/remote/actions`).
   - `apps_running` confirmed (`200`) and completed immediately (`state: "done"`, `needs_laptop_yes: false`, returned `[{"name": "Notepad", "title": "Untitled - Notepad"}]`).
   - `click` at `(200, 200)` confirmed from phone (`200`) and entered `state: "waiting_laptop"` (`needs_laptop_yes: true`), and the pending approval appeared on `/v3/approvals`.
   - Could not test tapping the Android app screen on a real phone because no phone was connected over ADB.

5. **Turn off Wi-Fi and use Prometheus offline — Partial**
   - Physical `Wi-Fi` was **not** disabled because it is the laptop's only `Up` network adapter and disabling it would sever this active session.
   - Verified local clone operation without remote calls: `POST /chats` returned `303`, `GET /v3/memory` returned `200`, and `cloudroom_storage.Storage.queue("private", "offline_note", ...)` + `.flush()` against an `Offline` transport kept `["offline_note"]` waiting in the encrypted outbox (`sent: 0, waiting: 1`, message: `"Offline: the uploads wait in the outbox and go when there is a connection."`).

6. **Move a small task to Cloudroom (asks first) — Partial**
   - Default settings had empty `cloud_url` and `cloudroom_url` (no live Render Cloudroom URL configured on this laptop).
   - Started `prometheus.runtime.cloudroom_server:app_from_env` from this clone on `127.0.0.1:8066`:
     - Starting without approval raised `RuntimeRefused`: `"Moving 'Add 2+3 in Cloudroom' to cloudroom needs your yes first."`
     - `cr.request_move` created a plain-English approval showing the code (`print(2 + 3)`), files (`none`), limits (`60 seconds`, `128 MB`), and `"None of your keys goes up."`
     - Resolving the approval (`"Moved to Cloudroom (job 1)."`) ran the job (`pending` -> `running` -> `done`) with result `{"ok": true, "output": "5", "timed_out": false, "files": []}`.
