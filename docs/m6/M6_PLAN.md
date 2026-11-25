# M6 plan: Runtime, Cloudroom, PC and phone control (25 Nov 2026)

Status: **plan written 25 Nov 2026.** M6 starts after M5's audit is accepted by the owner (PLAN.md rule 3).
Nothing here adds scope: these are the 5 steps of M6 in `docs/PLAN.md`, filled in with the owner's answers.
Work happens on `prometheus-v3`.

## Owner's answers (25 Nov 2026)
- **Cloudroom:** Oracle Cloud didn't work (it needs a credit card; the owner has a debit card). Replace it with a free
  service that needs **no card**. Chosen: **Hugging Face Spaces** (free "CPU basic" Space, private, Docker) for
  running moved tasks and as the new always-on cloud copy, and **Google Drive** (already used for backups) for
  storage. Step 6.0 checks the free-tier terms first; if they don't hold, Opus proposes the next option and asks.
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
| 6.0 | **Checks before building** | Hugging Face free-tier terms, mobile-mcp licence and version, any new package's licence (7-step check in AGENTS.md) written down with the date. Laptop checked for Node.js and adb. | — |
| 6.1 | **ExecutionRuntime** | One way to run tasks: `LocalRuntime` (default) runs every M5 job; `CloudroomRuntime` plugs in behind the same calls. | Every M5 job kind runs through `LocalRuntime` with the same results; nothing calls the old paths directly. |
| 6.2 | **Cloudroom** | A private free Space runs moved tasks in a sandbox. Moving asks first; each move has a request id and a receipt; events arrive in order; a lost link resumes or fails cleanly. The Space also takes over the old Oracle cloud copy's job. | Unplug mid-task → task resumes or is marked failed, never doubled; events out of order are put right; a move without a yes is refused. |
| 6.3 | **Cloudroom storage** | Repo knowledge, backups and sync data encrypted on the laptop before upload; the laptop copy is the main one; works offline. Includes the private notes of chats left out in Memory → Privacy. | Upload holds only scrambled bytes; offline use works; wrong key can't read; laptop copy wins on a clash. |
| 6.4a | **Control PC: screen, mouse, keyboard** | See the screen, click, type, switch apps on Windows. Every click, typed text or app change asks first. | Without a yes nothing is clicked; Stop all stops it at once. |
| 6.4b | **Control the laptop from the phone** | The phone app runs laptop actions remotely over the private link; each one asks on the phone first. | A request without the device key is refused; dangerous actions still ask. |
| 6.5 | **Phone control through mobile-mcp** | mobile-mcp runs as a separate MCP server through "Other apps"; old adb phone-control code removed once mobile-mcp does everything it did. Taps/typing/app changes ask first; reading the screen after a one-time yes. | Each old adb action has a mobile-mcp test; an unapproved tap is refused. |
| 6.6 | **Audit and report** | Fresh-session audit, fixes, `docs/reports/M6_REPORT.md`, CHANGELOG, missing.md, bugs.md. | — |

**Exit gate** (from PLAN.md): disconnect/reconnect tests; works offline; encryption tests; phone control works on the
owner's phone through mobile-mcp with approvals; GitHub Windows CI green (not the owner's laptop); audit passed;
open-source check of every new dependency.

## Work for each step
Each builder does the items in order, a failing test first each time.

### 6.0 Checks before building (Opus; laptop check: Antigravity)
1. Read Hugging Face's current pricing and terms: free CPU Space size, sleep rules, private Spaces, storage, whether a
   card is needed. Write the facts with links and the date. If a card is needed, stop and ask the owner.
2. Re-check mobile-mcp: licence (Apache-2.0?), latest version, what it needs (Node.js version).
3. Choose the screen/mouse/keyboard library for 6.4a (Windows UI Automation first; no new package if `ctypes` or an
   existing one is enough) and run the 7-step licence check on anything new.
4. Antigravity, on the laptop: report whether Node.js, npm and adb are installed and their versions. Install nothing.

### 6.1 ExecutionRuntime (builder: Opus)
1. `prometheus/runtime/base.py`: `ExecutionRuntime` with start, status, cancel, events, result.
2. `local.py`: `LocalRuntime` wraps M5's job runner, graph and limits; no behaviour change.
3. Every place that starts M5 jobs goes through the runtime; a lint test stops new direct calls.
4. A runtime choice per job, default local; Cloudroom only after an approval (6.2).

### 6.2 Cloudroom (builder: Opus · Space setup files: Sonnet)
1. `runtime/cloudroom.py`: talks to the owner's private Space over HTTPS with a signed token (stored in
   `KEY_NAMES`), never Prometheus's other keys.
2. Moving a task asks first, showing what goes up (files, text) and what it may do there.
3. Each request has an id and a receipt; events are numbered and put in order; a repeat request isn't run twice.
4. Lost link: retries, then resume from the last receipt or mark failed; nothing runs twice.
5. The Space runs tasks in a sandbox (no access to the token store; time and memory limits).
6. Sonnet: `cloud/space/` Dockerfile and a plain-words setup guide replacing the Oracle steps in
   `docs/cloud-setup.md`; the cloud copy's jobs (reminders, alerts, sync) run there; the Space's sleep rule written
   down honestly.

### 6.3 Cloudroom storage (builder: Sonnet · encryption review: Opus)
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

### 6.4b Control the laptop from the phone (builder: Sonnet · security review: Opus)
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
| 6.2 Space Dockerfile and setup guide | **Sonnet** | Glue and plain-words writing. |
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
| 3 | 6.5 mobile-mcp (phone plugged in) | 6.4b phone → laptop; Space files | 6.2 Cloudroom | Round 2 pushed |
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
