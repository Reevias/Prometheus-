# M5 plan: Many agents (draft, 22 Nov 2026)

Status: **plan agreed 22 Nov 2026** (owner's answers below). M5 starts only after M3 is accepted and M4 is built,
audited and accepted (PLAN.md rule 3). Nothing here adds scope: it is the 6 steps of M5 in `docs/PLAN.md`
plus the browser tool moved from M3. Work happens on `prometheus-v3`.

## What M5 gives the owner
Prometheus can run several jobs at once without losing any of them: every job is saved, can be stopped, and
carries on (or is marked failed) after a crash or restart. Big jobs are split into a graph of smaller ones.
The owner can make their own agents and see everything running on one screen.

## Steps, in build order

| # | Step | What "done" means | Tests that must fail first |
|---|---|---|---|
| 5.1 | **Job tracker** [F4] | One SQLite table of jobs: pending, running, paused, done, failed, cancelled. Every background task goes through it (the 29 files with raw `threading.Thread` / `asyncio.create_task` are moved over or listed as allowed). After a restart, "running" jobs become "paused" with a Resume button; nothing runs again by itself (owner, 22 Nov). `resume.py`'s queue becomes a user of the tracker. Stop all pauses every job. | Kill the process mid-job → on restart the job is paused, not lost. A lint test fails if a new untracked thread appears. |
| 5.2 | **Task graph (DAG)** | Steps with dependencies, run side by side or one after another; retries with waits; cancelling a parent cancels its children; a failed step stops the steps that need it. Cycles are refused. Saved in the tracker, so a graph survives a restart. | Cycle refused; cancel spreads; retry count kept after restart; failed parent → children "cancelled". |
| 5.3 | **Child agents at once** | A limit sized for 8 GB: default 2 at once (3 if free RAM > 3 GB, read with psutil, never faked). On Windows each child that runs a program goes in a Job Object with a memory cap. Start, status, clean-up; no orphans left after Stop all. | Start 5 jobs → only 2 run; RAM guard stops a 3rd when memory is low; Stop all leaves no child processes (checked on Windows CI). |
| 5.5 | **Multi-agent view** | Replaces M2's "running now" list: each job, its graph, live status, Stop / Pause / Resume buttons, real times only. Uses the existing Jinja2/HTMX/Canvas look and colours. Built before 5.4 so the owner can see agents. | Browser tests: buttons work, a cancelled job shows cancelled; no dead buttons. |
| 5.4 | **Your own agents** | Create, edit, remove agents: name, instructions, which tools, which model card (M3.4). Least privilege: a new agent gets no tools until ticked; dangerous tools still need approval. One switch in Features. | An own agent can't use an unticked tool; deleting an agent cancels its jobs. |
| 5.6 | **Conversation branching** | "Branch from here" on any message: a new chat copying history up to that point; the original stays unchanged. | Branch keeps the original intact; branching a branch works. |
| 5.7 | **Accessibility-tree browser tool** (moved from M3) | The browser agent can take a snapshot of the page's accessibility tree and act on its elements. Page text is wrapped as untrusted (M3's `has_untrusted`). | Snapshot of a test page; a page saying "ignore your rules" is treated as data. |
| 5.8 | **Audit + report** | Independent review, fixes, `docs/reports/M5_REPORT.md`, CHANGELOG, missing.md, bugs.md. | — |

**Exit gate** (from PLAN.md): parallel, cancel and restart tests pass; memory stays inside the laptop's limit;
GitHub Windows CI green (not the owner's laptop: the laptop run is separate and marked NOT VERIFIED until done);
audit passed; open-source check for any new dependency (none planned: SQLite, asyncio and psutil are already in).

## Who does what

The rule of thumb: **Opus decides and checks, Codex builds the well-defined pieces, Sonnet builds the UI and the
glue.** Opus never reviews its own code; whoever built a step doesn't audit it.

| Work | Model | Why |
|---|---|---|
| Write each step's exact spec and its fail-first test list before building | **Opus** | Design mistakes here are the costly ones. |
| 5.1 job table, states, restart recovery | **Codex** | Clear spec, mostly code + tests. |
| 5.2 DAG engine (cancel spread, retries, recovery) | **Opus** builds, **Codex** writes extra edge-case tests | The hardest logic in M5. |
| 5.3 concurrency limit, RAM guard, Windows Job Objects | **Opus** | Windows process handling + safety. |
| Moving the 29 untracked threads onto the tracker | **Codex** | Repetitive and mechanical, with tests. |
| 5.5 multi-agent view (HTML/HTMX/Canvas, browser tests) | **Sonnet** | UI work in the existing style. |
| 5.4 own agents (forms, storage, Features switch) | **Sonnet** | Mostly UI + plain storage. Opus checks the tool-permission part. |
| 5.6 conversation branching | **Codex** | Small, self-contained. |
| 5.7 accessibility browser tool | **Sonnet** builds, **Opus** reviews the untrusted-text handling | Security-sensitive input. |
| CHANGELOG, missing.md, bugs.md, report text | **Sonnet** | Writing in plain words. |
| Security review and final M5 audit | **Opus** (a fresh session) | Independent check. |

## Workflow for every step
1. **Opus** writes the step's spec and test list in this file (small section per step).
2. The builder (table above) does `git pull`, writes the failing tests, then the code, until they pass.
3. Full suite: `/home/user/venv314/bin/python -m pytest -q`, plus browser tests for UI steps.
4. Push to `prometheus-v3`; GitHub `windows-latest` run must be green.
5. A different model reviews the diff (Opus for Codex/Sonnet work; Sonnet + a fresh Opus session for Opus work).
6. Fix review findings with a failing test first; tick the step here; one-line short report to the owner.

Two builders never edit the same file at once; always `git pull` before starting and before pushing; never force-push.

## Owner's answers (22 Nov 2026)
- Jobs at once: 2, or 3 when more than 3 GB of RAM is free.
- After a restart or crash: running jobs wait as "paused"; the owner presses Resume.
- Codex is set up: its tasks are written as ready-to-paste prompts when each step starts.
