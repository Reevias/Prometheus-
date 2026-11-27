# Prometheus 3.0 — M6 report (one way to run tasks, Cloudroom, controlling the PC and the phone)

27 November 2026, branch `prometheus-v3`. **Merged into main on 27 Nov on the owner's instruction** (after the open items below were fixed). Almost everything here is **NOT VERIFIED on the owner's laptop, on Render, and on the phone** (what was checked
for real is listed below). `docs/m6/M6_LAPTOP_CHECK.md`, named in the request for this report, does not exist; the
laptop and phone facts come from the plan, `docs/missing.md`, the CHANGELOG and `docs/reports/M6_AUDIT.md`.

## What M6 does, in plain words
- **One way to run tasks (6.1).** Every job starts through `prometheus.runtime` (start, status, cancel, events,
  result). `LocalRuntime`, the laptop, is the default and runs M5's job runner, task graphs and limits as before. State
  changes are kept as numbered events in order. A job goes to another runtime only if that runtime is set up and your
  yes checks out. Always-on loops never move. A test stops new code from starting jobs another way.
- **Cloudroom (6.2).** A task can move to your free Render service (no card), only after you say yes on the Approvals
  page. The question shows exactly what goes up (each file's name and size, all the code) and what it may do there
  (Python in a sandbox, no internet, at most 300 seconds, 256 MB and 5 MB of files). The yes is for exactly that task
  and works once. Only the new "Cloudroom key" signs the messages and it never travels itself. Each move has a request
  id and a signed receipt, events come back numbered and in order, and a task is never run twice. On Render, one task
  runs at a time and a task can't read the key. After the audit the app connects Cloudroom when its address
  (`cloudroom_url`) and key are set.
- **Render files and guide (6.2 item 6).** `cloud/render/` has the Dockerfile, `render.yaml` and `requirements.txt`
  (only fastapi, uvicorn and psutil). `docs/cloud-setup.md` walks through Render, the uptime checker and Tailscale in
  plain words and states Render's limits honestly (sleeps after 15 minutes, 750 hours a month, files wiped on restart,
  512 MB). **The always-on cloud copy (reminders while the laptop is off) is not run by these files**: the free service
  has one web port, 512 MB, and forgets its files on every restart.
- **Cloudroom storage (6.3).** Switch in Features, off at the start. Repo knowledge, sync data and the private notes of
  chats you left out are sealed on the laptop (AES-256-GCM from backup.py, key from your recovery code) and kept in a
  "Prometheus Cloudroom" folder on Google Drive; the nightly backup goes there too. The laptop copy is the main one
  (on a clash the other copy is kept in "Other copies"). Offline, uploads wait in a queue and go later.
- **Control PC: screen, mouse, keyboard (6.4a).** `prometheus/pc_control/`: private screenshots, reading Windows UI
  controls (as untrusted text, password values never read), clicks, typing, allowed key combinations and switching
  windows. Reading the screen asks once; every click, typing, key press or app switch asks first and shows what it will
  do. Password fields and secret-looking text are refused; terminals are never touched; Stop all stops it within a
  second.
- **Control the laptop from the phone (6.4b).** Switch in Features, off at the start. A phone pairs with a one-time code
  (5 minutes, 5 tries) and gets its own key (the laptop keeps only a hash). Only that key works, only over the private
  link (Tailscale or the laptop itself, no proxy). Every action asks on the phone with a separate one-time yes; mouse
  and keyboard actions also ask on the laptop. Every step is in the activity log. There is no phone screen for it yet,
  only the web API.
- **Phone control through mobile-mcp (6.5).** `@mobilenext/mobile-mcp` 1.0.20 (Apache-2.0) runs as a separate program
  in "Other apps", listed in Credits. Reading the screen asks once; taps, typing and app or setting changes ask every
  time; secrets, password fields and terminal apps are refused. The old adb phone-control code is gone.

## Exit gate (docs/PLAN.md, M6)
From `docs/reports/M6_AUDIT.md`.

| Gate item | Result |
|---|---|
| Disconnect/reconnect tests | **Pass in tests; NOT VERIFIED on Render.** Lost link while waiting, answer lost after the server took the task, link down for good, Cloudroom restart after the receipt, laptop restart before and after the receipt, a proxy error page and a 401. Never run against a real Render service. |
| Works offline | **Pass in tests; NOT VERIFIED on the laptop.** Cloudroom storage queues offline and sends later; the queue is capped. |
| Encryption tests | **Pass.** AES-256-GCM with a per-file key, counter nonces, the piece number and "last piece" sealed in; Drive sees only a keyed tag; a wrong key can't open; files can't be swapped. Reviewed by reading the code too. |
| Phone control through mobile-mcp with approvals | **Partly.** See "Laptop and phone check". |
| GitHub Windows CI green | **Pass** (see below). GitHub's machines, not the owner's laptop. |
| Open-source check of new dependencies | **Pass.** No Python dependency added (`pyproject.toml` unchanged). New outside program: mobile-mcp 1.0.20, Apache-2.0, pinned, separate program, in Credits and `docs/m0/DEPENDENCY_MATRIX.md`. |
| Audit passed | **Done, but not fully independent**: run by the Opus session that built 6.1 and 6.2. A separate reviewer agent took a second look at 6.1 and 6.2, and the session reviewed 6.3–6.5 (built by Sonnet and Antigravity, already reviewed once). |

## Numbers, and where they ran
| Where | What | Result |
|---|---|---|
| GitHub, `a2151fb` (run 45966776964) | ubuntu-latest 3.14 / 3.15 | green; 2109 passed, 49 skipped (5 min 29 s / 4 min 1 s) |
| GitHub, `a2151fb` | windows-latest 3.14 / 3.15 | green; 2104 passed, 54 skipped (11 min 37 s / 14 min 12 s) |
| GitHub, `a2151fb` | Click through the interface (Windows) | green; 26 passed (9 min 25 s) |
| GitHub, `a2151fb` | Windows add-ons 3.14 | green |
| Here (Linux cloud, Python 3.14.6) | full suite on `a2151fb` | 2143 passed, 0 failed, 70 skipped (5 min 45 s) |
| Owner's laptop and phone | M6 | **not run as a suite**; partial hand checks below |

The audit notes one full-suite run with one failure in an M5 test (`test_status_shows_state_run_time_and_real_memory`,
under load); 15 repeats and the next full run passed. The report's own commit is not included in these numbers.

## Laptop and phone check (what was really tried)
- **6.0, on the owner's Windows laptop (25 Nov):** Node.js 24.21.0, npm 12.2.2, adb 1.0.41 and `UIAutomationClient` in
  Windows PowerShell 5.1 are present (nothing installed).
- **6.4a, laptop (Windows 11):** window listing, the virtual screen size, terminal blocking, reading the controls of a
  test window and Stop all ending a PowerShell process in under a second passed. **NOT VERIFIED live:** a screenshot
  of the unlocked desktop, switching windows, typing into a real box and refusing a real password field (the screen
  was locked during the test).
- **6.5, plugged-in OnePlus Nord 5 (Android 16), 26 Nov:** mobile-mcp found the device, listed apps, opened Calculator,
  took a real 30,966-byte screenshot, read all 36 controls, tapped once and pressed Home. The phone locked part way, so
  several taps, typing, the password refusal and reading Settings or Clock are **NOT VERIFIED live** (fake mobile-mcp
  tests only).
- **Not tried at all:** a real Render service (build, start, a moved task), Cloudroom storage with the owner's real
  Google Drive, control from a real phone (6.4b), the Docker build.

## Findings and fixes (bugs 199–232, 34 in all; each fixed with a test that failed first)
**Review of 6.3–6.5, 26 Nov (Opus, did not build them): bugs 199–218.**
- Approvals for PC control and phone reading had no handler after a restart, so a yes failed; Stop all wasn't hooked
  into PC control until it first ran.
- **Critical:** a window title reached PowerShell inside quotes that curly quotes could end, so an AI-chosen title could
  run a command. It now goes in as base64.
- The yes for typing showed only 37 of up to 500 characters; single keys and Ctrl+V could go into a password box;
  shrunk screenshots didn't say their scale.
- Control from the phone: typed text showed in the laptop's pop-up; phone laptop-actions ignored the "apps" switch; a
  waiting laptop yes still ran after the switch was turned off; a cancel could slip in during a confirm.
- Phone control: the screen was read before the one-time read yes; an approved tap ran even if the button had moved;
  the approvals hid the number really used and that an SMS text isn't filled in; Stop all didn't stop between the steps
  of an action; mobile-mcp wasn't pinned and its command could be changed without the password; requests waiting for
  the phone never resumed (removed adb code was still called).
- Cloudroom storage: Drive got the plain-text SHA-256 of each item; a sealed file moved to another name was accepted;
  the offline queue grew without limit; notes of deleted or re-included chats stayed on Drive; two uploads could run at
  once.

**Audit, 27 Nov: bugs 219–232.**
- **High:** a task running in Cloudroom could start a shell (`_posixsubprocess.fork_exec` raises no audit event) and
  read the Cloudroom key from the server's start-up environment in /proc. Checked as a real non-root user here: before
  the fix the task started a shell, after it the kernel refused. Fix: tasks run with a process limit of 0 (the Render
  image runs as a non-root user) and the server process is made not dumpable.
- **Medium:** a proxy error or a 401 failed a running task as "restarted"; a move cut off by a laptop restart stayed
  "pending" for ever (now paused, with Resume); a watcher that broke left the task "running"; the 512 MB server read
  huge unsigned bodies, kept every task's uploads and read result files whole.
- **Low:** the CPU limit was fixed at 65 s whatever the task's limit; two watchers after Pause and Resume; a file
  changed between the check of the yes and the send; the question cut the code at 600 characters; a failed move kept
  its yes; Cloudroom wasn't connected in the app.

## After this report: the audit's open items fixed (27 Nov, bugs 233–236)
- **Cloudroom is usable:** Settings → Connections → Cloudroom (Render) (under "Show advanced settings") takes the Render
  address and key behind your password and connects at once. Ask for a script to run "in Cloudroom": the code agent
  asks on the Approvals page (all the code and the files are shown), and after your yes the answer comes back to the
  same chat.
- **Stop really stops on Render:** the sandbox checks for Stop while a task runs and ends its program at once; the next
  task doesn't wait.
- **The key goes to Render as a Secret File** (`/etc/secrets/CLOUDROOM_TOKEN`), not an environment variable;
  `render.yaml` and the guide say so.
- **Linux sandbox:** a kernel filter (seccomp, x86-64 and ARM64) refuses starting any other program from a sandboxed
  script, by any route, even as root; threads still work. (Windows: the sandbox's Job Object already allows one process.)
- Full suite after these: 2154 passed, 0 failed, 70 skipped (Linux here).

## Your part (what you need to add)
1. A Render account (free, no card) and the service from `cloud/render/render.yaml` (`docs/cloud-setup.md`, steps 1–5).
2. Your Cloudroom key as a Render **Secret File** named `CLOUDROOM_TOKEN`, then Deploy.
3. The Render address and the same key in Settings → Connections → Cloudroom (Render), with your password.
4. Optional: a free uptime checker visiting `<address>/v1/health` every 10 minutes (keeps Render awake).
5. Optional: Tailscale on the laptop and phone, then pair the phone to control the laptop from it.
6. When you have time, a laptop check of M6: PC control, phone control (typing, several taps, a password box) and a
   first real Cloudroom task.

## Residual risks
- The always-on cloud copy has no home yet; reminders don't ring while the laptop is off. There is no phone app screen
  for control from the phone (only the web API).
- On a Linux CPU other than x86-64 or ARM64 the sandbox's program block isn't installed.
- A stolen device key can give the phone yes itself (the laptop yes still guards clicks and typing); no rate limit for a
  paired phone; device keys can't be rotated (revoke and pair again).
- PC control: `win` alone, `alt+f4` and `ctrl+w` are guarded only by the per-action yes; the window under the cursor
  isn't re-checked for clicks; `read_controls` shows ordinary text-box values to the AI; the process isn't DPI-aware.
- Phone: the app list is read before asking; `setting` only opens the right Settings page; npx gets the laptop's
  environment variables; "look" sends phone screenshots to a cloud vision AI by default (old behaviour; the router
  still asks before any paid AI).
- Cloudroom storage: Drive rollback to an older copy of an item isn't detected; a new recovery code doesn't re-seal
  unchanged items; a Google 5xx stops the rest of one upload run (nothing is lost). Without your recovery code the Drive
  files can't be opened.
- The audit was not fully independent (the owner chose not to run another session).
- Nothing has run on a real Render service yet.

## Merge
Merged into `main` on 27 November 2026 on the owner's instruction, after the checks above. The laptop, phone and Render
checks are still to be done (NOT VERIFIED).
