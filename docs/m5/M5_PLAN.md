# M5 plan: Many agents (22 Nov 2026)

Status: **plan agreed 22 Nov 2026** (owner's answers below). M3 and M4 are merged into main; M5 starts after M4's
separate checker and the owner's laptop check (PLAN.md rule 3). Nothing here adds scope: it is the 6 steps of M5 in `docs/PLAN.md`
plus the browser tool moved from M3. Work happens on `prometheus-v3`.

## What M5 gives the owner
Prometheus can run several jobs at once without losing any of them: every job is saved, can be stopped, and
carries on (or is marked failed) after a crash or restart. Big jobs are split into a graph of smaller ones.
The owner can make their own agents and see everything running on one screen.

## Steps, in build order

| # | Step | What "done" means | Tests that must fail first |
|---|---|---|---|
| [x] 5.1 | **Job tracker** [F4] | One SQLite table of jobs: pending, running, paused, done, failed, cancelled. Every background task goes through it (the 30 files with raw `threading.Thread` / `asyncio.create_task` are moved over or listed as allowed). After a restart, "running" jobs become "paused" with a Resume button; nothing runs again by itself (owner, 22 Nov). `resume.py`'s queue becomes a user of the tracker. Stop all pauses every job. | Kill the process mid-job → on restart the job is paused, not lost. A lint test fails if a new untracked thread appears. |
| 5.2 | **Task graph (DAG)** | Steps with dependencies, run side by side or one after another; retries with waits; cancelling a parent cancels its children; a failed step stops the steps that need it. Cycles are refused. Saved in the tracker, so a graph survives a restart. | Cycle refused; cancel spreads; retry count kept after restart; failed parent → children "cancelled". |
| 5.3 | **Child agents at once** | A limit sized for 8 GB: default 2 at once (3 if free RAM > 3 GB, read with psutil, never faked). On Windows each child that runs a program goes in a Job Object with a memory cap. Start, status, clean-up; no orphans left after Stop all. | Start 5 jobs → only 2 run; RAM guard stops a 3rd when memory is low; Stop all leaves no child processes (checked on Windows CI). |
| 5.5 | **Multi-agent view** | Replaces M2's "running now" list: each job, its graph, live status, Stop / Pause / Resume buttons, real times only. Uses the existing Jinja2/HTMX/Canvas look and colours. Built before 5.4 so the owner can see agents. | Browser tests: buttons work, a cancelled job shows cancelled; no dead buttons. |
| 5.4 | **Your own agents** | Create, edit, remove agents: name, instructions, which tools, which model card (M3.4). Least privilege: a new agent gets no tools until ticked; dangerous tools still need approval. One switch in Features. | An own agent can't use an unticked tool; deleting an agent cancels its jobs. |
| 5.6 | **Conversation branching** | "Branch from here" on any message: a new chat copying history up to that point; the original stays unchanged. | Branch keeps the original intact; branching a branch works. |
| 5.7 | ✅ **Accessibility-tree browser tool** (moved from M3) | The browser agent can take a snapshot of the page's accessibility tree and act on its elements. Page text is wrapped as untrusted (M3's `has_untrusted`). | Snapshot of a test page; a page saying "ignore your rules" is treated as data. |
| 5.8 | **Audit + report** | Independent review, fixes, `docs/reports/M5_REPORT.md`, CHANGELOG, missing.md, bugs.md. | — |

**Exit gate** (from PLAN.md): parallel, cancel and restart tests pass; memory stays inside the laptop's limit;
GitHub Windows CI green (not the owner's laptop: the laptop run is separate and marked NOT VERIFIED until done);
audit passed; open-source check for any new dependency (none planned: SQLite, asyncio and psutil are already in).

## Work for each step

Each list is the whole job for that step. The builder does the items in order, a failing test first each time.
Files named are where the work starts; the builder may add files next to them.

### 5.1 Job tracker [x] (builder: Antigravity · spec + review: Opus)
1. New `prometheus/jobs/` package: `store.py` (SQLite table `jobs`: id, kind, title, state, parent_id, created,
   started, finished, progress text, error, retries, payload JSON) using the existing database and M1's atomic writes.
2. `states.py`: the allowed moves only (pending→running→done/failed/cancelled/paused; paused→pending on Resume).
   Any other move raises an error.
3. `runner.py`: `start_job(kind, func, ...)` runs work in a tracked thread or asyncio task and records every change.
   Progress is only what the job really reports.
4. On start-up: every job left "running" becomes "paused" with the reason "Prometheus restarted". Nothing restarts
   by itself.
5. Stop all (`safety/killswitch.py`) pauses every job; Resume all only lifts the stop, jobs stay paused until resumed.
6. Move `resume.py`'s retry queue onto the tracker (same waits as now).
7. Move the 30 files that start raw `threading.Thread` / `asyncio.create_task` onto `start_job`. Ones that must stay
   (the web server, the hotkey listener and similar) go in a short allow-list with the reason written next to each.
8. Lint test: a new raw thread or task outside the allow-list fails the suite.

### 5.2 Task graph (builder: Opus · extra edge-case tests: Antigravity)
1. `prometheus/jobs/graph.py`: a graph is a parent job; its steps are child jobs with a "needs" list.
2. Checks when a graph is made: no loops, no missing steps, at most 50 steps.
3. Runs every step whose needs are done; independent steps run side by side (within 5.3's limit).
4. Retries per step (default 2, waits of 10 s then 60 s); the retry count is saved, so a restart doesn't reset it.
5. A failed step marks the steps that need it "cancelled (needed step failed)"; other branches carry on.
6. Cancelling the graph cancels every step not yet finished; pausing it pauses them.
7. After a restart the graph is paused as a whole; Resume carries on from the finished steps.
8. The M3 orchestrator can hand a multi-part plan to the graph instead of running it all in one loop.

### 5.3 Child agents at once (builder: Opus)
1. `prometheus/jobs/limits.py`: at most 2 child agents at once, 3 when psutil shows more than 3 GB of free RAM.
   Extra jobs wait as "pending (waiting for a free slot)".
2. Checks RAM again before each start; under 1 GB free, nothing new starts and the reason is shown.
3. Any program a child starts on Windows goes in a Job Object with a memory cap and "kill on close" (reusing M1's
   terminal Job Object code); on Linux, its own process group.
4. Clean-up: when a job ends, is cancelled or Stop all is pressed, all its processes are ended; a check at start-up
   ends any left over from a crash.
5. Status of each child: state, how long it has run, its real memory use.
6. Windows CI test: Stop all leaves no child processes behind.

### 5.5 Multi-agent view (builder: Sonnet · review: Opus)
1. Replace the "running now" list in `web/templates/v3/agents.html` with the full view.
2. Each job: title, agent, state, real start time and run time, progress text, memory use.
3. A graph shown as a small step diagram on the Canvas (done, running, waiting, failed, cancelled in the M2 colours).
4. Stop, Pause and Resume buttons on jobs and graphs, wired to real routes; a button only appears when the move is
   allowed (no dead buttons).
5. Updates through HTMX every few seconds; nothing shown that wasn't read from the tracker.
6. Works on the phone layout too.
7. Browser tests for every button and for a cancelled and a failed job.

### 5.4 Your own agents (builder: Sonnet · tool-permission check: Opus)
1. Storage for own agents: name, short description, instructions, ticked tools, model card (from M3.4's table).
2. Create, edit and remove from the Agents page; one switch "Own agents" in the Features list.
3. A new agent has no tools until ticked; tools that need approval still ask every time.
4. The check is in code: the orchestrator refuses any tool call the agent hasn't been given.
5. Own agents can be picked with `@name`, like built-in ones, and can run as child agents (5.3).
6. Removing an agent cancels its running jobs first (asks before doing it).
7. Names can't copy a built-in agent's name.

### 5.6 Conversation branching (builder: Antigravity · review: Opus)
1. "Branch from here" on any message in a chat.
2. Makes a new chat with the history up to that message (in `memory/store.py`); the original is not changed.
3. The new chat shows where it came from, with a link back.
4. Branching a branch works; deleting the original keeps the branch.
5. Tests for each of the above.

### 5.7 [x] Accessibility-tree browser tool (builder: Sonnet · untrusted-text review: Opus) — built 24 Nov, awaiting Opus review
1. In `agents/browser_agent.py`: a snapshot tool returning the page's accessibility tree (role, name, state) with
   short element ids, built on Playwright's own snapshot.
2. Click, type and select by those ids, checking the element still exists first.
3. All page text goes back to the model wrapped as untrusted (`has_untrusted`); a page saying "ignore your rules"
   stays data.
4. A size limit on snapshots, so a huge page doesn't fill the model's memory.
5. Listed in the tool list with where it runs (laptop).

### 5.8 Audit and report (Opus, a fresh session · writing: Sonnet)
1. Independent review of all M5 changes: bugs, security, Windows behaviour, the 8 GB limit.
2. Each finding fixed with a failing test first.
3. Exit gate checks run and written down with real numbers.
4. `docs/reports/M5_REPORT.md`, CHANGELOG, `docs/missing.md`, `docs/bugs.md` updated; laptop check listed as NOT
   VERIFIED until the owner runs it.

## Review of 5.1 and 5.7 (Opus, 24 Nov)
Checked against the work lists, item by item. Tests: see the report line at the end.

**5.1 Job tracker**
1. Store: done (the `jobs` table sits in `memory.db`, sharing its connection and lock).
2. Allowed moves: done.
3. Runner: done. *Partly:* cancel and pause only change the record; a thread that is already running isn't
   stopped (Python threads can't be stopped from outside). Needs a "should I stop?" check jobs call: 5.2/5.3.
4. Restart → paused: done after fixes R1 and R2. *Partly:* Resume only moves a job to "pending"; nothing runs
   pending jobs yet. That is the scheduler of 5.2/5.3 (Opus builds it next); the button itself is 5.5.
5. Stop all pauses jobs, Resume all keeps them paused: done after fix R2.
6. `resume.py` on the tracker: done after fix R3.
7. Threads moved: done, except `connectors/browser.py` (round 2, as planned) and 5 that slipped through (fix R4).
8. Lint test: done after fix R4.

**5.7 Browser tool**: items 1, 2, 4 and 5 done; item 3 done after fixes R6 and R7.

**Findings, all fixed with a test that failed first** (bugs 172–179 in `docs/bugs.md`):
- R1. The start-up check ran after the chat-archive job of the same start had begun, so it was marked
  "paused: Prometheus restarted". The tracker now opens straight after the database, before any job starts.
- R2. The 13 always-on loops (reminders, sync, watchers ...) were jobs like any other: Stop all showed them
  "paused" while they kept running, and every restart left 13 more "paused" jobs whose Resume could do nothing.
  They now start with `service=True`: Stop all leaves them, a restart closes the old record as cancelled.
- R3. Waiting requests (AI limit) stayed "paused" for ever: when answered, and when given up (paused → failed
  isn't allowed and the error was hidden). New `JobStore.finish()` ends a job through the allowed moves only.
- R4. The thread check missed `threading.Timer`, thread pools, `loop.create_task`, `ensure_future`, Thread
  subclasses and `import threading as _th`, and allowed any number of threads in a listed file. It slipped
  through: backup, safety check, voice button (now tracked) and 2 push sends (listed with reasons). The check
  (`prometheus/jobs/lint.py`) now counts every call: one reason per allowed call.
- R5. The runner kept every finished thread while Prometheus ran; now only the last 64.
- R6. Clicking by id put the page's button name in the AI's message as plain text. The AI now gets the id and
  the name wrapped as untrusted; the owner's question shows the name shortened to 120 characters.
- R7. Error text written by the page ("The web page had a problem: ...") reached the AI as plain text; now wrapped.

**Left open (honest list)**
- Cancel/pause don't stop running threads yet, and Resume has nothing that runs pending jobs (5.2/5.3).
- Finished job rows are never tidied away (small, but they grow: one per message for the intent check).
- 5.7: elements inside iframes aren't in the snapshot; `pyproject.toml` still says `playwright>=1.45` although
  snapshots need a newer one (an older one gives a plain "run pip install -U playwright" message); a page can
  still lie about its own buttons (the click rules read what the page reports).
- Not run on the owner's laptop or on Windows by me: NOT VERIFIED until GitHub's windows-latest run is green.

## Who does what

The rule of thumb: **Opus decides and checks, Antigravity builds the well-defined pieces, Sonnet builds the UI and the
glue.** Opus never reviews its own code; whoever built a step doesn't audit it.

| Work | Model | Why |
|---|---|---|
| Write each step's exact spec and its fail-first test list before building | **Opus** | Design mistakes here are the costly ones. |
| 5.1 job table, states, restart recovery | **Antigravity** | Clear spec, mostly code + tests. |
| 5.2 DAG engine (cancel spread, retries, recovery) | **Opus** builds, **Antigravity** writes extra edge-case tests | The hardest logic in M5. |
| 5.3 concurrency limit, RAM guard, Windows Job Objects | **Opus** | Windows process handling + safety. |
| Moving the 30 untracked threads onto the tracker | **Antigravity** | Repetitive and mechanical, with tests. |
| 5.5 multi-agent view (HTML/HTMX/Canvas, browser tests) | **Sonnet** | UI work in the existing style. |
| 5.4 own agents (forms, storage, Features switch) | **Sonnet** | Mostly UI + plain storage. Opus checks the tool-permission part. |
| 5.6 conversation branching | **Antigravity** | Small, self-contained. |
| 5.7 accessibility browser tool | **Sonnet** builds, **Opus** reviews the untrusted-text handling | Security-sensitive input. |
| CHANGELOG, missing.md, bugs.md, report text | **Sonnet** | Writing in plain words. |
| Security review and final M5 audit | **Opus** (a fresh session) | Independent check. |

## Order and who works when (24 Nov)
Two builders can work at the same time because their files don't overlap.

| Round | Antigravity | Sonnet | Opus |
|---|---|---|---|
| 1 | 5.1 Job tracker (all items; **leave `connectors/browser.py` alone** until Sonnet's 5.7 is pushed) | 5.7 Browser tool (`agents/browser_agent.py`, `connectors/browser.py`) | — |
| 2 | 5.6 Branching; then move `connectors/browser.py` onto the tracker | Docs for round 1 | Reviews 5.1 and 5.7 |
| 3 | Edge-case tests for 5.2 | — | Builds 5.2, then 5.3 |
| 4 | Fixes from reviews | 5.5 view, then 5.4 own agents | Reviews 5.5 / 5.4 permission check |
| 5 | — | Report text | Fresh-session audit (5.8) |

Shared files (`CHANGELOG.md`, `docs/bugs.md`, `docs/missing.md`): `git pull` first, add your own lines only, keep
the other builder's lines if git shows a clash.

## Workflow for every step
1. **Opus** writes the step's spec and test list in this file (small section per step).
2. The builder (table above) does `git pull`, writes the failing tests, then the code, until they pass.
3. Full suite: `/home/user/venv314/bin/python -m pytest -q`, plus browser tests for UI steps.
4. Push to `prometheus-v3`; GitHub `windows-latest` run must be green.
5. A different model reviews the diff (Opus for Antigravity/Sonnet work; Sonnet + a fresh Opus session for Opus work).
6. Fix review findings with a failing test first; tick the step here; one-line short report to the owner.

Two builders never edit the same file at once; always `git pull` before starting and before pushing; never force-push.

## Owner's answers (22 Nov 2026)
- Jobs at once: 2, or 3 when more than 3 GB of RAM is free.
- After a restart or crash: running jobs wait as "paused"; the owner presses Resume.
- Codex is used up (24 Nov): **Antigravity takes every Codex job.** It runs on the owner's Windows laptop, so its
  test runs there count as a real laptop run (say so in its report; GitHub's Windows run is still needed).
- Each builder works from the step's work list below; the owner pastes a short start message.
