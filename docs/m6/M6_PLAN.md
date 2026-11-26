# M6 plan: Runtime, Cloudroom, PC and phone control (25 Nov 2026)

Status: **plan written 25 Nov 2026; 6.0 findings added the same day; owner chose Render (free, no card) with an uptime pinger instead of Hugging Face.** M6 starts after M5's audit is accepted by the owner (PLAN.md rule 3).
Nothing here adds scope: these are the 5 steps of M6 in `docs/PLAN.md`, filled in with the owner's answers.
Work happens on `prometheus-v3`.

## Owner's answers (25 Nov 2026)
- **Cloudroom:** Oracle Cloud didn't work (it needs a credit card; the owner has a debit card). Replace it with a free
  service that needs **no card**. Chosen at first: **Hugging Face Spaces** (free "CPU basic" Space, private, Docker) for
  running moved tasks and as the new always-on cloud copy, and **Google Drive** (already used for backups) for
  storage. Step 6.0 checks the free-tier terms first; if they don't hold, Opus proposes the next option and asks.
  **Changed 25 Nov after the 6.0 check:** Hugging Face now needs a paid plan, so Cloudroom runs on **Render's free web
  service** (no card) with a free uptime pinger; see "6.0 findings".
- **Control PC:** both: (a) Prometheus sees the screen and clicks/types on the laptop, and (b) the phone app controls
  the laptop remotely.
- **What may move to Cloudroom:** any task, but **every move asks the owner first**.
- **Node.js and adb on the laptop:** not known. Antigravity checks first; nothing is installed without asking.

## What M6 gives the owner
One way to run every task: on the laptop by default, or in Cloudroom when the owner says yes. Prometheus's knowledge
is stored encrypted off the laptop and still works offline. Prometheus can use the laptop's mouse and keyboard and the
phone (through mobile-mcp), always asking first, and the phone can control the laptop.

## Steps, in build order

| # | Step | What "done" means | Tests that must fail first |
|---|---|---|---|
| [x] 6.0 | **Checks before building** | Hugging Face free-tier terms, mobile-mcp licence and version, any new package's licence (7-step check in AGENTS.md) written down with the date. Laptop checked for Node.js and adb. | — |
| [x] 6.1 | **ExecutionRuntime** | One way to run tasks: `LocalRuntime` (default) runs every M5 job; `CloudroomRuntime` plugs in behind the same calls. | Every M5 job kind runs through `LocalRuntime` with the same results; nothing calls the old paths directly. |
| 6.2 | **Cloudroom** | The owner's free Render web service runs moved tasks in a sandbox. Moving asks first; each move has a request id and a receipt; events arrive in order; a lost link resumes or fails cleanly. It also takes over the old Oracle cloud copy's job. | Unplug mid-task → task resumes or is marked failed, never doubled; events out of order are put right; a move without a yes is refused. |
| [x] 6.3 | **Cloudroom storage** | Repo knowledge, backups and sync data encrypted on the laptop before upload; the laptop copy is the main one; works offline. Includes the private notes of chats left out in Memory → Privacy. | Upload holds only scrambled bytes; offline use works; wrong key can't read; laptop copy wins on a clash. |
| [x] 6.4a | **Control PC: screen, mouse, keyboard** | See the screen, click, type, switch apps on Windows. Every click, typed text or app change asks first. | Without a yes nothing is clicked; Stop all stops it at once. |
| [x] 6.4b | **Control the laptop from the phone** | The phone app runs laptop actions remotely over the private link; each one asks on the phone first. | A request without the device key is refused; dangerous actions still ask. |
| [x] 6.5 | **Phone control through mobile-mcp** | mobile-mcp runs as a separate MCP server through "Other apps"; old adb phone-control code removed once mobile-mcp does everything it did. Taps/typing/app changes ask first; reading the screen after a one-time yes. | Each old adb action has a mobile-mcp test; an unapproved tap is refused. |
| 6.6 | **Audit and report** | Fresh-session audit, fixes, `docs/reports/M6_REPORT.md`, CHANGELOG, missing.md, bugs.md. | — |

**Exit gate** (from PLAN.md): disconnect/reconnect tests; works offline; encryption tests; phone control works on the
owner's phone through mobile-mcp with approvals; GitHub Windows CI green (not the owner's laptop); audit passed;
open-source check of every new dependency.

## 6.0 findings (Opus, 25 Nov 2026)
Checked on 25 Nov 2026 from the official pages linked below. Plan only: no code.

### Hugging Face Spaces: **not free any more for what we need. Stopped here (owner decides).**
Source: [Spaces overview](https://huggingface.co/docs/hub/spaces-overview), [pricing](https://huggingface.co/pricing),
[ZeroGPU](https://huggingface.co/docs/hub/spaces-zerogpu).
- **The blocker:** "Static Spaces are free for everyone. **Gradio and Docker Spaces run on compute and require a paid
  plan to create: PRO** for personal accounts." PRO is $9/month, paid by card. So the free private Docker Space the plan
  chose can't be made on a free account.
- CPU Basic itself: 2 vCPU, 16 GB RAM, 50 GB disk that is **not kept** (wiped on restart); "no hourly cost", but only
  inside a paid plan now.
- Sleep: on free hardware a Space "goes to sleep and stops executing after a period of time if unused" (no exact time
  given on these pages).
- Private Spaces: yes (code and app visible only to the owner). Networking: outbound only on ports 80, 443, 8080.
- Free accounts can only host up to 2 **ZeroGPU Gradio** Spaces (verified email, account older than 30 days, 5 GPU
  minutes a day). These are for AI demos with Gradio, not a server that runs Prometheus's tasks and reminders: not
  usable for Cloudroom.
- Static Spaces are free but run no server code: not usable.

### Next-best free option with **no card**: Render (free web service)
Source: [Render: Deploy for Free](https://render.com/docs/free) (official docs, read 25 Nov 2026); "no credit card"
also stated in [Render's own comparison](https://render.com/articles/platforms-with-a-real-free-tier-for-developers-in-2026).
- Free web service (Python works; Docker too), no card needed to sign up and run it.
- Spins down after **15 minutes** without an incoming request; waking takes about **1 minute**.
- **750 free hours a month** per account (one service kept awake all month uses 744 h, so only one always-on service).
- **Files are wiped** on every restart, redeploy or spin-down; no persistent disk on free. The free Postgres database
  **expires after 30 days** (then deleted 14 days later). So storage must stay on **Google Drive** (already the plan for
  6.3) and the laptop.
- No SSH, single instance, may be restarted at any time; outbound mail ports 25/465/587 blocked; very heavy outbound
  traffic can get a free service suspended. Render itself says "do not use for production" (it is fine for a personal
  hobby project).
- Public URL (HTTPS): every request must carry Prometheus's signed token (6.2 already plans this).
- What this means for M6: Cloudroom tasks and the cloud copy can run on Render, but **reminders while the laptop is off
  only fire if something keeps the service awake** (for example the phone app or a free uptime pinger every 10 min,
  which uses the month's 750 hours on that one service). This needs the owner's OK.
- Not chosen: Fly.io (card needed, no free tier for new users, per the same comparison), Railway (one-time $5 credit,
  then paid), Oracle (card, already tried), Hugging Face PRO (card).

### mobile-mcp (for 6.5)
Source: npm registry `@mobilenext/mobile-mcp` and the repo's README/LICENSE (github.com/mobile-next/mobile-mcp), read
25 Nov 2026.
- Licence: **Apache-2.0** (LICENSE file and npm agree). Latest version **1.0.20**, published 24 Nov 2026.
- Needs **Node.js 20 or newer** (`engines: node >=20`) and, for an Android phone, **Android Platform Tools (`adb`)** with
  USB debugging switched on and allowed. (Xcode only for iPhone simulators; not needed.)
- Its own dependencies checked for licence: `mobilewright` 0.0.72 (Apache-2.0), `add-mcp` 2.6.1 (Apache-2.0); the rest
  are well-known MIT/ISC packages (express, zod, ajv, commander, qs, fast-xml-parser, the MCP SDK).
- 7-step check (AGENTS.md): 1 Apache-2.0; 2 allows use, change, sharing, commercial use; 3 compatible with MIT and it
  runs as a **separate program** through "Other apps" (not copied into Prometheus); 4 no field-of-use limits, includes a
  patent grant; 5 actively released (new version the day before this check); 6 to be written in Credits and
  `docs/m0/DEPENDENCY_MATRIX.md` with version 1.0.20; 7 it is the open-source choice. **Passes.** Still to check on the
  laptop (Antigravity): whether Node.js 20+ and adb are installed (install nothing without asking).

### Screen, mouse and keyboard on Windows (for 6.4a): **no new package**
- `prometheus/laptop.py` already takes screenshots and types/presses keys through PowerShell scripts (with the
  foreground-window check). 6.4a builds on that.
- **Reading the screen's controls:** Windows' own UI Automation through .NET's `UIAutomationClient`, which ships with
  Windows (loaded in the existing PowerShell scripts with `Add-Type -AssemblyName UIAutomationClient`). No download.
- **Clicking and moving the mouse:** `ctypes` calls to Windows' `user32` (`SendInput`, `SetCursorPos`), so a click is
  instant and Stop all can stop it within a second (no new process per click).
- **Switching apps:** `ctypes` (`EnumWindows`, `SetForegroundWindow`) or the existing PowerShell way.
- **Screenshots:** Pillow (already a dependency) `ImageGrab` or the existing PowerShell script.
- So the 7-step check isn't needed. Fallback only if UI Automation through PowerShell is too slow on the laptop:
  `comtypes` 1.5.0 (MIT, Enthought, released 9 Nov 2026), which would get its own 7-step check first. Not chosen:
  `pywinauto` 0.6.9 (BSD-3, but pulls in pywin32 too), `uiautomation` 2.0.29 (Apache-2.0, but a larger wrapper over
  comtypes).
- NOT VERIFIED on Windows: whether `UIAutomationClient` loads in the laptop's PowerShell (Windows PowerShell 5.1 should
  have it); Antigravity checks this along with Node.js and adb.

### Owner's decision (25 Nov 2026)
- **Cloudroom runs on Render's free web service** instead of Hugging Face (no card). Storage stays on the laptop and,
  encrypted, on Google Drive (Render forgets its files on every restart).
- **A free uptime pinger** visits the Render copy every 10 minutes so reminders and alerts fire while the laptop is
  off (it uses that month's 750 free hours on this one service; the pinger gets no access to any data). The setup
  guide shows each step; nothing is set up without asking.
- Still open for the laptop (Antigravity): Node.js 20+, adb and `UIAutomationClient` in PowerShell; install nothing.

### 6.0 laptop check (Antigravity, 25 Nov 2026)
Checked on the owner's Windows laptop (nothing installed):
- `node -v`: **`v24.21.0`** (installed; meets `mobile-mcp`'s `node >= 20` requirement).
- `npm -v`: **`12.2.2`** (installed).
- `adb version`: **installed** — `Android Debug Bridge version 1.0.41`, `Version 37.0.3-16887417`, at `C:\Users\saive\AppData\Local\Microsoft\WinGet\Packages\Google.PlatformTools_Microsoft.Winget.Source_8wekyb3d8bbwe\platform-tools\adb.exe` (`Running on Windows 10.0.26100`).
- `UIAutomationClient` in Windows PowerShell 5.1: **loads cleanly** (`Add-Type -AssemblyName UIAutomationClient` succeeded, `RootElement` available, `ClassName=#32769`).

## Work for each step
Each builder does the items in order, a failing test first each time.

### 6.0 Checks before building [x] (Opus; laptop check: Antigravity) — done 25 Nov
1. Read Hugging Face's current pricing and terms: free CPU Space size, sleep rules, private Spaces, storage, whether a
   card is needed. Write the facts with links and the date. If a card is needed, stop and ask the owner.
2. Re-check mobile-mcp: licence (Apache-2.0?), latest version, what it needs (Node.js version).
3. Choose the screen/mouse/keyboard library for 6.4a (Windows UI Automation first; no new package if `ctypes` or an
   existing one is enough) and run the 7-step licence check on anything new.
4. Antigravity, on the laptop: report whether Node.js, npm and adb are installed and their versions. Install nothing.

### 6.1 ExecutionRuntime [x] (builder: Opus) — built 25 Nov, awaiting review
1. `prometheus/runtime/base.py`: `ExecutionRuntime` with start, status, cancel, events, result.
2. `local.py`: `LocalRuntime` wraps M5's job runner, graph and limits; no behaviour change.
3. Every place that starts M5 jobs goes through the runtime; a lint test stops new direct calls.
4. A runtime choice per job, default local; Cloudroom only after an approval (6.2).

### 6.2 Cloudroom (builder: Opus · Render setup files: Sonnet) — items 1–5 [x] built 25 Nov (Opus), awaiting review; item 6 Sonnet
1. `runtime/cloudroom.py`: talks to the owner's Render service over HTTPS with a signed token (every request without it is refused) (stored in
   `KEY_NAMES`), never Prometheus's other keys.
2. Moving a task asks first, showing what goes up (files, text) and what it may do there.
3. Each request has an id and a receipt; events are numbered and put in order; a repeat request isn't run twice.
4. Lost link: retries, then resume from the last receipt or mark failed; nothing runs twice.
5. The Render service runs tasks in a sandbox (no access to the token store; time and memory limits).
   Free Render machines are small: **512 MB RAM** (confirmed on render.com/pricing, 25 Nov 2026; the CPU share isn't
   stated, the smallest paid one has 0.5 CPU). So a task gets at most 256 MB and 300 seconds, no internet, 5 MB of files. Before a move, check the task fits; if it doesn't, say so plainly and keep it on the laptop.
   Render can restart at any time and wipe files, so a moved task keeps its progress through receipts, never only on
   Render's disk.
   **Built (items 1–5):** `prometheus/runtime/cloudroom.py` (laptop side), `cloudroom_server.py` (runs on Render:
   `prometheus.runtime.cloudroom_server:app_from_env`, token from Render's secret file `/etc/secrets/CLOUDROOM_TOKEN`
   or the `CLOUDROOM_TOKEN` variable; files under `/tmp/cloudroom`; `GET /v1/health` for the pinger, needs no key),
   `cloudroom_protocol.py` (signing). New key `CLOUDROOM_TOKEN` in `KEY_NAMES`.
6. [x] (built 26 Nov, Sonnet; the always-on cloud copy is NOT hosted by these files, see `docs/cloud-setup.md`) Sonnet: `cloud/render/` files (render.yaml or Dockerfile) and a plain-words setup guide replacing the Oracle steps in
   `docs/cloud-setup.md`, including the free uptime pinger every 10 minutes; the cloud copy's jobs (reminders, alerts,
   sync) run there; Render's limits written down honestly (sleeps after 15 min without the pinger, 750 h a month, files
   wiped on restart, so nothing is kept only there).

### 6.3 [x] Cloudroom storage (builder: Sonnet · encryption review: Opus) — built 25 Nov, awaiting review
1. Reuse `backup.py`'s AES-256-GCM: everything encrypted on the laptop before upload.
2. Store repo knowledge (M4), backups and sync data on Google Drive in a "Prometheus Cloudroom" folder.
3. Laptop copy is the main one; on a clash the laptop wins and the other copy is kept as a backup.
4. Works offline: uploads wait and go later.
5. The private notes of chats left out in Memory → Privacy go up encrypted and can be looked up later.
6. One switch in Features: "Cloudroom storage".

### 6.4a Control PC: screen, mouse, keyboard (builder: Antigravity · safety review: Opus)
1. `prometheus/pc_control/`: take a screenshot, read the screen's controls (UI Automation), click, type, press keys,
   switch apps.
2. Every click, typed text, key press or app change asks first and shows what it will do; reading the screen asks once.
3. Never types into password fields; never sends text that looks like a secret.
4. Stop all stops it at once; a running action ends within a second.
5. Tested on the laptop itself (it's Windows); also on GitHub's Windows run.

### 6.4b [x] Control the laptop from the phone (builder: Sonnet · security review: Opus) — built 26 Nov, awaiting review
1. From the phone app: list of laptop actions (the fixed ones in `laptop.py`, open apps, lock screen, 6.4a actions).
2. Only over the private link with the paired device key; others are refused.
3. Each action asks for a yes on the phone; dangerous ones (6.4a clicks/typing) also show on the laptop.
4. A record of every remote action in the activity log.

### 6.5 Phone control through mobile-mcp (builder: Antigravity · review: Opus)
1. After the owner says yes, set up Node.js and adb if missing (the installer checks and shows how).
2. Add mobile-mcp as a separate server in "Other apps"; not copied into the code; listed in Credits with its licence.
3. Approvals: taps, typed text and app changes ask every time; reading the screen after a one-time yes.
4. Match every action of the old adb code (`phone.py`, `agents/phone_agent.py`) with mobile-mcp, with a test each.
5. Then remove the old adb control code and its tests; keep anything still used elsewhere.
6. Real run on the owner's phone, written down.

### 6.6 Audit and report (Opus, a fresh session · writing: Sonnet)
1. Independent review of all M6 changes: security (tokens, approvals, encryption, remote control), Windows behaviour,
   offline use.
2. Each finding fixed with a failing test first.
3. Exit gate checks run and written down with real numbers.
4. `docs/reports/M6_REPORT.md`, CHANGELOG, `docs/missing.md`, `docs/bugs.md`; anything not run on the laptop or the
   phone is NOT VERIFIED.

## Who does what
**Opus decides, builds the risky core and checks; Antigravity does the Windows and phone hands-on work on the laptop;
Sonnet builds storage, the phone screen and the glue.** Nobody audits their own work.

| Work | Model | Why |
|---|---|---|
| 6.0 terms and licence checks | **Opus** | Decisions with money/licence risk. |
| 6.0 laptop tool check | **Antigravity** | It's on the laptop. |
| 6.1 ExecutionRuntime | **Opus** | Core that everything else uses. |
| 6.2 Cloudroom runtime (receipts, order, recovery) | **Opus** | Hardest logic + security. |
| 6.2 Render files, pinger and setup guide | **Sonnet** | Glue and plain-words writing. |
| 6.3 encrypted storage | **Sonnet** builds, **Opus** reviews encryption | Reuses existing backup code. |
| 6.4a screen/mouse/keyboard | **Antigravity** | Must be tried on real Windows. |
| 6.4b phone → laptop | **Sonnet** builds, **Opus** reviews security | UI + plain routes. |
| 6.5 mobile-mcp | **Antigravity** | Needs the laptop and the phone plugged in. |
| Laptop and phone check | **Antigravity** | Real hardware. |
| Docs, CHANGELOG, report text | **Sonnet** | Writing. |
| Final audit | **Opus** (fresh session) | Independent. |

## Order and who works when
| Round | Antigravity (laptop) | Sonnet | Opus | Start when |
|---|---|---|---|---|
| 1 | 6.0 laptop tool check | — | 6.0 terms and licence checks | M5 accepted |
| 2 | 6.4a PC control | 6.3 storage | 6.1 ExecutionRuntime | Owner OK on 6.0's findings |
| 3 | 6.5 mobile-mcp (phone plugged in) | 6.4b phone → laptop; Render files | 6.2 Cloudroom | Round 2 pushed |
| 4 | Laptop + phone check | Docs | Reviews 6.3, 6.4a, 6.4b, 6.5 | Round 3 pushed |
| 5 | — | Report | Fresh-session audit; a second fresh session reviews 6.1/6.2 | Round 4 fixed |

Files don't overlap inside a round: Opus in `prometheus/runtime/` and `cloud/`; Antigravity in `prometheus/pc_control/`,
`phone.py`, `agents/phone_agent.py`, connector settings; Sonnet in storage, the phone app's screens and docs.
Shared files (`CHANGELOG.md`, `docs/bugs.md`, `docs/missing.md`): `git pull` first, own lines only, keep both on a clash.

## Workflow for every step
1. The builder does `git pull`, writes the failing tests, then the code, until they pass.
2. Full suite (`/home/user/venv314/bin/python -m pytest -q`, or `python -m pytest -q` on the laptop) plus browser tests
   for UI steps.
3. Push to `prometheus-v3`; GitHub `windows-latest` run must be green.
4. A different model reviews; findings fixed with a failing test first; tick the step; short report to the owner.
Never force-push; never touch main; ask before installing anything; never fake results.
