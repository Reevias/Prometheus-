# M5 audit (25 November 2026)

Branch `prometheus-v3`. Auditor: Opus, in the session that also **built 5.2 and 5.3 and reviewed 5.1, 5.4, 5.5 and
5.7**: this is **not** the independent fresh-session audit the plan asks for. To make up for that, a separate reviewer
agent that hadn't seen my reasoning read every M5 change (7 of its 8 findings fixed below, 1 written down). The owner
may still want a fresh session to check this work.

## Exit gate (docs/PLAN.md, M5)

| Gate item | Result | True numbers |
|---|---|---|
| Parallel, cancel and restart tests pass | **PASS (Linux, GitHub Windows)** · laptop NOT VERIFIED | Full suite on Linux: 1941 passed, 0 failed, 67 skipped. GitHub windows-latest: see the CI line below. The laptop run of 25 Nov (before these fixes) had 1 failed and 1 hang, both caused by risk 1; not re-run on the laptop since. |
| Memory stays inside the 8 GB laptop's limit | **PASS on Linux measurement** · laptop NOT VERIFIED | At most 2 children at once (3 above 3 GB free, 1 under 1 GB), and the same for chat tasks. Prometheus with 200 graph steps (3 at once, 20 MB each): 165–246 MB peak RSS on Linux, of which 22.7 MB of Python objects were still held after the work (the rest is the C allocator keeping freed memory). Programs run through the M1 terminal get a 1.5 GB Job Object cap; the code sandbox keeps its own limit. The laptop had 0.23–0.84 GB free with the owner's apps open; Prometheus's own memory on the laptop was not measured. |
| Windows CI green | **PASS** (after a hang was found and fixed) | GitHub run 45594385450 for `68ab218`, run twice: all 6 jobs green both times. windows-latest 3.14: 1906 passed, 48 skipped (9 min 45 s and 13 min 44 s), and no test was stuck for more than 2 minutes (stack dumps now switched on). Before that, windows-latest 3.14 hung at 88% twice (runs for `9f82fa7` and `75dde82`, cancelled after 30 min to read the logs), and failed once on a too-short test wait. GitHub's Windows machines are not the owner's laptop. |
| Open-source check of new dependencies | **PASS** | No dependency added in M5 (`pyproject.toml` unchanged since the M5 plan, d284517). |

## The four risks from the laptop check (all fixed, a test failing first)
1. **Under 1 GB free nothing ever started** and parallel tasks waited for ever. Now: one at a time under 1 GB free (a
   change from the plan's "none"), and tests use a fixed memory reading instead of the machine's real one.
2. **Stop all race** (orphan program, stopped step marked done). Now: jobs are paused first, then programs ended
   (`limits.connect_stop_all`); the runner never records the end of a graph step itself; a graph starts nothing while
   everything is stopped; a program started during a stop is ended at once.
3. **Resume after restart**: a stopped or restarted chat task gets **Run again** (asks first; refused while
   everything is stopped, for a deleted chat, or while that chat is busy). Orchestrator graphs still close after a
   restart: their task's job carries the Run again.
4. **Chats bypassed the limit**: chat tasks now have their own limit of the same size (separate from child agents,
   so a task can never wait for a slot its own steps need).

## Audit findings (all fixed, a test failing first; bugs 182–195)
- A1. The graph runner kept every finished graph's functions and context (a whole task's results) in memory.
- F1. Pausing or stopping a chat task's graph from the panel ended the task as "done" with half its steps, and Resume
  could run steps twice. A chat task's graph now has Stop only (it stops the task); a graph that ends paused or
  cancelled ends the task as stopped and is closed.
- F2. After Stop all, jobs that then finished stayed "paused" for ever. They are now recorded as they really ended.
- F3. A branch of a chat left out of memory became searchable. The branch is left out too.
- F4. The code sandbox a child agent ran wasn't known to the clean-up (the 5.3 report said a child's programs were
  ended by cancel and Stop all; that was only true for the M1 terminal, which no agent uses). Now it is.
- F5. One try's clean-up could end the next try's programs; Windows now closes the Job Object instead of looking up a
  process number.
- F6. The job table only grew and the panel read all of it every 4 s. Old finished jobs are tidied after 30 days;
  the panel, own-agent removal and the start-up clean-up read only what they need.
- F7. A request retried after an AI limit was recorded "done" even when it failed.
- A2. My A1 fix added an idle check that took two locks in the opposite order to the graph driver: GitHub's Windows 3.14 run hung at 88% (cancelled after 30 minutes to read its log). Fixed; a test now catches the wrong order.
- A4. The hang came back once after A2 and A3. The likely cause was my F5 change, which made Windows end a child's programs by closing the Job Object only; it is back to the way that passed in 5.3 (taskkill, then close the Job Object). Two green runs since, but an intermittent hang can't be proved gone: the CI now prints every thread's stack if one test runs over 2 minutes, and stops the test step after 40 minutes.
- A3. The same lock-order problem in the job runner's Stop/Pause/Resume (from 5.1): the panel's Stop on a plain job could freeze with a running graph. Fixed with the same kind of test.

## Open items, finished afterwards (25 Nov, bugs 196–198, each with a test failing first)
- F8. Paid AI calls now reserve their most possible cost before they run, under one lock, for both the cap on paid
  AI used without asking and the task's own spending limit, which is now checked before every paid call (not only
  between steps). Steps running at the same time can no longer pass the same check and all spend. Calls to an AI
  without a model card can't be estimated, so they count only once they have spent.
- A graph step that never checks "should I stop?" now stops at its next AI call, sandbox program or terminal
  command after it is cancelled, paused or stopped. Python work between those still can't be interrupted.
- A removed own agent stops at its next round, also when it runs as a chat step outside the job tracker.

## Still open
- Everything here is NOT VERIFIED on the owner's laptop until the laptop check is run again.
