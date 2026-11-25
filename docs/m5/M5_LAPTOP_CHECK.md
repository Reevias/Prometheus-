# M5 Laptop Check (Windows 11, 8 GB RAM)

**Date:** 25 November 2026  
**Branch / commit tested:** `prometheus-v3` at `f075968`  
**Clone tested:** `C:\Users\saive\prometheus-m5` (isolated `PROMETHEUS_HOME` so the installed copy and owner data were untouched; verified `prometheus.__file__` imported from `C:\Users\saive\prometheus-m5\prometheus\__init__.py`)  
**Machine:** Windows 11 Home Single Language (24H2, x86_64), 12th Gen Intel Core i3-1215U, 7.69 GiB RAM, Python 3.14.10 (`C:\Users\saive\Prometheus-\.venv\Scripts\python.exe`).  
**Available RAM during tests:** 0.23 GB to 0.84 GB free (89%–97% used with normal apps open).  
**Code changes:** None (check-only run).

---

## 1. Full test suite (`python -m pytest -q`)

| Run | Exact numbers | Outcome / Notes |
|---|---|---|
| Unmodified `python -m pytest -q` (1,982 collected) | Stopped at test #298 (`14%`) | **Hung forever** on `tests/test_m5_own_agents.py::test_own_agents_run_as_child_agents_in_a_task_graph` |
| `python -m pytest -q --deselect tests/test_m5_own_agents.py::test_own_agents_run_as_child_agents_in_a_task_graph` | **1 failed, 1938 passed, 42 skipped, 1 deselected, 4 warnings** in 2388.61s (0:39:48) | Completed the rest of the suite |

### Why pytest hung / failed on this laptop
Both issues trace to the same real condition on an 8 GB Windows laptop: with ~0.27–0.40 GB of free RAM available during testing (`< 1 GB`, `LOW_MEMORY` in `prometheus/jobs/limits.py`), real `psutil.virtual_memory().available` prevents `RamSlots.try_acquire()` from ever granting a slot:

1. **Hang — `tests/test_m5_own_agents.py::test_own_agents_run_as_child_agents_in_a_task_graph`:**  
   `app_client` wires `GraphRunner` with real `child_slots()` (`RamSlots()`). Because free RAM was under 1 GB, neither step ever started, while `Orchestrator._execute_graph` looped forever in `while True: state = graphs.wait(gid, 0.2)`.
2. **Failure — `tests/test_m5_own_agents.py::test_removing_an_agent_asks_first_and_then_cancels_its_running_jobs`:**  
   `AssertionError: assert 'pending' == 'running'` at `tests/test_m5_own_agents.py:414`. Both steps stayed `'pending'` (stuck on low-RAM slots) instead of moving to `'running'`; after confirming delete of `mybot`, `other` (`research`) was still `'pending'` instead of `'running'`.

---

## 2. Live run from this clone

Started server from `C:\Users\saive\prometheus-m5` via `python -m prometheus.cli init --password-stdin` and `python -m prometheus.cli serve --host 127.0.0.1 --port 8055` (note: `python -m prometheus serve` fails with `No module named prometheus.__main__`; the CLI module is `prometheus.cli`), driven with headless Playwright Chromium and verified in Windows SQLite + `psutil` process listings.

### Summary

| Check | Result | What happened |
|---|---|---|
| Start 5 jobs at once (only 2 or 3 should run) | **Mixed / Failed as-is under `< 1 GB` free RAM** | **0 of 5** started in a 5-step `TaskGraph` under real `psutil` RAM (`0.30 GB` free < `1 GB`); **5 of 5** started at once for 5 chat `/api/v3/send` requests (not limited by `RamSlots`); with simulated `2.0 GB` free RAM in `RamSlots`, **only 2** child jobs ran while 3 waited `pending` |
| Stop all (no programs left in Task Manager) | **FAILED (race bug)** | Stopping a 5-step `TaskGraph` (`POST /kill` / `killswitch.trigger`) left **Child 3** running with **2 live `python.exe` processes** in Windows while Prometheus stayed up |
| Restart Prometheus (jobs show "paused" with Resume) | **Mixed** | **Works** for resumable registered-kind graphs (`paused` with **Resume** + **Stop**); plain `task` jobs show `paused` with **Stop only (no Resume)**; in-memory `Orchestrator` graphs turn **`cancelled`** on restart |
| Branch a chat | **Passed** | UI `Branch` button, copied prefix history, provenance chip/link, original unchanged, branch-of-branch, and original deletion all worked |
| Make an own agent with one tool | **Passed** | Disabled until `Settings → Features`, 0 tools ticked by default, created `notes_helper` with `read_files` only, persisted across restart |

---

### Detailed observations

#### A. Start 5 jobs at once
1. **Real laptop RAM (`0.30 GB` free, `96.1%` used):** Starting a 5-step parallel `TaskGraph` (`GraphRunner` with default `RamSlots()`) launched **0 child processes**. The parent graph stayed `running` with progress `"Waiting: not enough free memory (0.3 GB free; a new one starts when 1 GB is free)"`, and all 5 steps (`s1`–`s5`) sat in `pending` (`"Waiting for a free slot"`).
2. **Sending 5 chat jobs via UI/API (`POST /api/v3/send` on chats 4–8):** All **5** plain `task` jobs started in state `running` immediately (`Task in chat 4..8`), bypassing the 2-or-3 limit because `Services.start_task()` uses `start_job("task", ...)` directly without `RamSlots`.
3. **5-step child-process `TaskGraph` with `RamSlots` allowed `2.0 GB` free:** Only **2** child jobs ran at once (`Child 1`, `Child 2`, reported in `/v3/agents` as `45 MB memory`: `22 MB` + `23 MB`), while `Child 3`–`Child 5` waited as `pending · Waiting for a free slot`. In Windows process listings (`psutil`), the 2 running children appeared as 4 `python.exe` processes (each `.venv\Scripts\python.exe` shim + its base `Python314\python.exe` child).

#### B. Stop all (Task Manager cleanup) — FAILURE FOUND
- **What failed:** When `Stop all` (`POST /kill` / `killswitch.trigger()`) ran on the live 5-step `TaskGraph` (with `Child 1` and `Child 2` running real Windows `python.exe` subprocesses and `Child 3`–`Child 5` waiting `pending`):
  - **In two separate live runs** (HTTP server `POST /kill` and standalone probe `killswitch.trigger()`), **2 `python.exe` child processes stayed alive in Windows** (`[20568, 28944]` for `Child 3`) **after Stop all completed**. (They only died later if/when the Prometheus parent process exited and closed the Windows Job Object.)
  - **Child 1 was wrongly marked `done` instead of `paused`** in SQLite.
- **Exact cause observed in code & SQLite timestamps:**
  1. `prometheus/app.py:430-431` registers `job_limits.connect_killswitch(killswitch)` (`end_all`) **before** `job_store.pause_all(...)`.
  2. When `Stop all` triggers, `killswitch._event` is set and `end_all()` runs `taskkill /F /T` on `Child 1` and `Child 2`.
  3. `Child 1`'s subprocess exits, `_run_step` sees `ctx.should_stop() == True` (`killswitch.is_set == True`) and returns `None` early without changing `Child 1`'s DB state. Because `pause_all()` has not run yet, `JobRunner._start_sync_job` (`prometheus/jobs/runner.py:96-98`) sees `Child 1.state == RUNNING` and marks `Child 1` as **`done`**.
  4. `Child 1`'s `finally:` releases its slot and wakes `GraphRunner._drive()`. Because `_drive()` (`prometheus/jobs/graph.py:283-286`) only checks `parent.state != RUNNING` and does not check `killswitch.is_set`, `_drive()` immediately grabs the freed slot (3 ms later in SQLite) and **launches `Child 3`** *after* `end_all()` has already finished iterating `_PROGRAMS`.
  5. `pause_all()` then runs and sets the parent graph, `Child 2`, and newly spawned `Child 3` to `paused`, leaving `Child 3`'s Windows subprocess alive and orphaned until server exit.

#### C. Restart Prometheus (paused jobs & Resume)
- **Resumable registered-kind `TaskGraph` (`kind="laptop.sleep_child"`):** After hard-killing (`proc.kill()`) the live server mid-graph and restarting against the same `PROMETHEUS_HOME`, **0 child processes** were left in Windows, `/v3/agents` showed `5 Child Agents Graph` in state **`paused`** (`"Prometheus restarted"`) with both **`Resume`** and **`Stop`** buttons, and clicking **`Resume`** resumed running `Child 1` and `Child 2` with `Child 3`–`Child 5` queued `pending`.
- **Plain `task` jobs (chats 4–8 from `/api/v3/send`):** After `Stop all` + server restart, `/v3/agents` listed all 5 as **`paused`** (`"Stopped by user: Stopped by the user"`), with **only a `Stop` button and no `Resume` button** (expected per `prometheus/web/jobs_view.py:60-61`).
- **In-memory `TaskGraph` (like `Orchestrator` plans using Python functions):** After crash + restart, `recover_graphs()` marked the graph and steps **`cancelled`** (`"Prometheus restarted, so this can't carry on by itself: run it again."`), not `paused` with Resume.

#### D. Branch a chat
- Opened seeded chat `/c/1` (`Original Laptop Check Chat`, 3 messages), clicked `Branch` on message 2 (`POST /api/v3/chats/1/messages/2/branch` returned `200`).
- Navigated to `/c/2`: showed 2 copied messages and topbar chip `"From: Original Laptop Check Chat"` linking to `/c/1`. `/c/1` kept all 3 messages unchanged.
- Clicked `Branch` on `/c/2` message 1 (`POST /api/v3/chats/2/messages/4/branch` returned `200`) -> `/c/3` with 1 message and chip `"From: Original Laptop Check Chat (branch)"`.
- Deleted `/c/1` (`POST /chats/1/delete` returned `204`) -> `/c/2` kept `"From: Original Laptop Check Chat"` rendered as a plain `<span>` (no broken link).

#### E. Make an own agent with one tool
- Before switching on `Features`, `/v3/agents` hid the creation form and pointed to `Settings → Features`.
- Toggled `"Own agents"` (`own_agents`) on in `/settings` (`/settings/feature` returned `200`).
- Opened `/v3/agents?own=new`: verified `0` tools were ticked by default.
- Created `notes_helper` (`about`: `"Reads text notes for me"`, `instructions`: `"Summarize notes concisely."`, only `read_files` ticked).
- Redirected to `/v3/agents?show=notes_helper`; detail pane showed `Read` permission only (no `Web search` / `Web fetch` / `Open`), saved in `settings.json`, and persisted cleanly across server restart.
