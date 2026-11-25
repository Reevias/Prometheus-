# Prometheus 3.0 — M5 report (many agents at once)

25 November 2026, branch `prometheus-v3`. **Done on the branch, waiting for the owner to merge.** Not merged into
main. Everything here is **NOT VERIFIED on the owner's laptop** after the last fixes (see "Your laptop").

## What M5 does, in plain words
- **Job tracker (5.1).** Every background job is saved in one SQLite table (`jobs`: pending, running, paused, done,
  failed, cancelled) and can only move in allowed ways. When Prometheus starts, jobs left "running" become "paused"
  with the reason "Prometheus restarted"; nothing restarts by itself. Stop all pauses every job; Resume all only lifts
  the stop. The retry queue for AI limits (`resume.py`) and the files that started raw threads now go through the
  tracker, and a check in the test suite (`prometheus/jobs/lint.py`) fails if a new raw thread or task appears outside
  a short list with a reason written next to each entry. The always-on loops (reminders, sync, ...) are marked as
  services, so Stop all and restarts leave them alone.
- **Task graphs (5.2).** A big job can be a graph: a parent job with steps that wait on each other. Graphs with a
  loop, a missing step or more than 50 steps are refused. Steps that don't need each other run side by side. A
  failing step is tried again after 10 s and then 60 s (the count is saved); a failed step cancels only the steps that
  need it. Cancel and pause spread to every step. After a restart a graph waits as paused and Resume carries on from
  the finished steps. The M3 orchestrator can hand a plan with independent steps to a graph, behind a Features switch
  ("Run a task's independent steps at the same time"), **off** for now.
- **Child agents at once (5.3).** At most 2 at the same time, 3 when more than 3 GB of RAM is free, read with psutil
  before every start. Programs a child starts go, on Windows, in a Job Object with a 1.5 GB memory cap that ends them
  when it closes (on Linux, their own process group). Cancel, pause and Stop all end them; at the next start,
  programs left by a crash are ended (only if the number still belongs to the same program). The status of each child
  shows its state, how long it has run and the real memory of its programs.
- **Your own agents (5.4).** On the Agents page: a name, a one-line description, instructions, ticked tools and a model
  card. A new agent has **no tools**. Four tools can be ticked (web search, read a web page, read a text file, open a
  file). The check is in code (`OwnAgent._call`): the tool must be ticked and allowed by the same permission table as
  the built-in agents; opening a file asks on Approvals every time; what a page or file says reaches the AI as
  untrusted data; after a file is read, the rest uses the laptop's own AI only and the web tools are refused. `@name`
  picks one; it can run as a graph step. Names are letters and underscores and can't copy a built-in agent's name.
  Removing an agent asks first and cancels only that agent's jobs. One switch, "Own agents" in Features (off at the
  start); nothing was added to Settings.
- **Multi-agent view (5.5).** The Agents page shows every job and graph read from the tracker (title, agent, state,
  real start and run time, progress, memory only when a real program exists). A graph is drawn on a Canvas in the M2
  colours. Pause, Resume, Stop and "Run again" appear only when that move is allowed and really does something.
  Updates every 4 seconds; fits a phone.
- **Conversation branching (5.6).** "Branch from here" on any message makes a new chat with the history up to there,
  with a link back; branching a branch works; deleting the original keeps the branch (the link becomes plain text).
- **Browser snapshot (5.7).** The browser agent can take an accessibility-tree snapshot of a page (role, name, state,
  short ids like `e12`, cut at 20 000 characters) and click, type or choose by those ids after checking the element is
  still there. The same pay/send/password rules apply. A typed password never shows in a snapshot; everything a page
  says (text, title, button names, error text) reaches the AI wrapped as untrusted.

## Exit gate (docs/PLAN.md, M5)
From `docs/reports/M5_AUDIT.md`.

| Gate item | Result |
|---|---|
| Parallel, cancel and restart tests pass | **Linux and GitHub Windows: pass.** The laptop run of 25 Nov (at `f075968`, before the audit fixes) had 1 failed and 1 hang; both came from risk 1 below, fixed since. Not re-run on the laptop. |
| Memory stays inside the 8 GB laptop's limit | **Pass on the Linux measurement; laptop NOT VERIFIED.** Prometheus with 200 graph steps (3 at once, 20 MB each): 165–246 MB peak RSS on Linux. Programs run through the M1 terminal get a 1.5 GB cap; the code sandbox keeps its own limit. On the laptop free RAM was 0.23–0.84 GB with the owner's apps open; Prometheus's own memory there was not measured. |
| Windows CI green | **Pass**, after a hang was found and fixed (see the CI history below). |
| Open-source check of new dependencies | **Pass.** No dependency was added in M5 (`pyproject.toml` unchanged since the M5 plan). |
| Audit passed | **Done, but not independent**: the audit was written by the Opus session that also built 5.2 and 5.3 and reviewed 5.1, 5.4, 5.5 and 5.7. A separate reviewer agent read every M5 change (7 of its 8 findings fixed, 1 written down). The owner may still want a fresh session to check this work. |

## Numbers, and where they ran
| Where | What | Result |
|---|---|---|
| Here (Linux cloud, Python 3.14.6) | full suite at the M5 audit | 1941 passed, 0 failed, 67 skipped |
| Here | full suite on the last commit, `534c0cf` (this report's own commit is not included) | 1949 passed, 0 failed, 67 skipped (6 min 13 s, Python 3.14.6) |
| GitHub, `534c0cf` (run 45618935538) | ubuntu-latest 3.14 / 3.15 | green; 3.14: 1916 passed, 46 skipped (4 min 35 s) |
| GitHub, `534c0cf` | windows-latest 3.14 / 3.15 | green; 3.14: 1914 passed, 48 skipped (11 min 9 s) |
| GitHub, `534c0cf` | Click through the interface (Windows), Windows add-ons 3.14 | both green |
| Owner's laptop (Windows 11, 8 GB), `f075968`, 25 Nov | `python -m pytest -q`, 1982 collected | hung at 14% on `test_own_agents_run_as_child_agents_in_a_task_graph`; with that test left out: 1 failed, 1938 passed, 42 skipped, 1 deselected (39 min 48 s). Not re-run since the fixes. |
| M5's own new tests | `tests/test_m5_*` and `tests/test_v3_m5_*` (jobs, graphs, limits, view, own agents, snapshot, branching, review and audit tests), part of the suite | see the suite |

**Windows CI history (true, from the audit).** GitHub's windows-latest 3.14 hung at 88% twice (runs for `9f82fa7` and
`75dde82`, cancelled after 30 minutes to read the logs) and failed once on a too-short test wait. Run 45594385450 for
`68ab218` was green twice (1906 passed, 48 skipped; 9 min 45 s and 13 min 44 s). CI now prints every thread's stack if
a test runs over 2 minutes and stops the test step after 40 minutes. GitHub's Windows machines are not the owner's
laptop.

## Findings and fixes (bugs 171–198, 28 in all; each fixed with a test that failed first)
**Step 5.7, while building (bug 171).** The browser agent gave a page's title to the AI as plain text.

**Opus review of 5.1 and 5.7, 24 Nov (bugs 172–179).**
- The start-up check ran after a job of the same start had begun and marked it "restarted".
- The 13 always-on loops showed as "paused" after Stop all and left 13 more "paused" jobs at each restart.
- Waiting requests (AI limit) stayed "paused" for ever, answered or given up.
- The thread check missed timers, thread pools, loop tasks and renamed imports; five threads slipped through.
- The runner kept every finished thread while running.
- A button name and an error text written by a web page reached the AI as plain text.

**Opus review of 5.4 and 5.5, 25 Nov (bugs 180–181).**
- An own agent with "read a file" and a web tool could read a file and send its words to the web. After a file is read,
  the web tools are refused.
- "Stop" on a request waiting after an AI limit left it in the retry queue, so it ran again by itself.

**Laptop check of 25 Nov (four risks, bugs 182–185).**
1. Under 1 GB free nothing ever started and parallel tasks waited for ever (the tests read the real RAM and hung).
   Now one at a time under 1 GB free. This changes the plan's "none".
2. Stop all could leave a program running and mark a stopped step "done". Now jobs are paused first, then programs
   are ended; nothing new starts while everything is stopped.
3. A stopped or restarted chat task had no way to run again. Now there is a "Run again" button (asks first).
4. Chats sent at the same time bypassed the limit. Chat tasks now have their own limit of the same size.

**Audit, 25 Nov (bugs 186–195).**
- The graph runner kept every finished graph's functions and results in memory.
- Pausing or stopping a chat task's graph from the panel ended the task as "done" with half its steps. Now such a
  graph has Stop only.
- After Stop all, jobs that then finished stayed "paused" for ever.
- A branch of a chat left out of memory became searchable.
- The code sandbox a child ran wasn't known to the clean-up (the 5.3 claim was only true for the M1 terminal, which no
  agent uses). Now it is.
- One try's clean-up could end the next try's programs.
- The jobs table only grew and the panel read all of it every 4 seconds (old finished jobs are tidied after 30 days).
- A request that ran again after an AI limit was recorded "done" even when it failed.
- Two lock-order problems (the graph's idle check, and the job runner's Stop/Pause/Resume) froze GitHub's Windows run;
  fixed, and a test now catches the wrong order. The hang came back once after the first fixes; the likely cause was
  an audit change to how Windows ends a child's programs, put back to the way that passed in 5.3 (taskkill, then close
  the Job Object). Two green runs since, but an intermittent hang can't be proved gone, which is why CI now dumps
  stacks.

**Open items finished afterwards, 25 Nov (bugs 196–198).**
- Steps running side by side could pass the same spending check and go over the task's limits together. Each paid
  call now reserves its most possible cost first, and the task's limit is checked before every paid call. An AI with
  no model card can't be estimated and counts only once it has spent.
- A cancelled or paused graph step that never checks "should I stop?" now stops at its next AI call, sandbox program
  or terminal command.
- A removed own agent now stops at its next round, also when it runs as a chat step outside the tracker.

## NOT VERIFIED ON WINDOWS (on your laptop)
The laptop check of 25 Nov (`docs/m5/M5_LAPTOP_CHECK.md`) found the four risks above; their fixes have not been run on
the laptop. Run the same checks again:
- Start 5 jobs at once: only 2 (or 3) should run; with little free memory, one at a time.
- Stop all: no programs left in Task Manager, no step marked "done" that was stopped.
- Restart Prometheus: graphs that can carry on show "paused" with Resume; a stopped chat task shows Run again.
- Branch a chat, and make an own agent with one tool.
- The full `python -m pytest -q`, which hung on the laptop at `f075968`.
- Prometheus's own memory on the laptop (not measured).
- The snapshot of a real page in Microsoft Edge (tests used Chromium).

## Residual risks
- A running Python thread can't be stopped from outside. Graph steps stop at their next check, AI call, sandbox
  program or terminal command; plain work in between can't be interrupted, and a plain running job has no Stop.
- Nothing runs a plain job that was Resumed ("pending"): only graphs resume for real.
- The 1.5 GB memory cap applies to commands run through the M1 terminal, which no agent uses yet; on Linux there is no
  cap, only a process group.
- Chat tasks that run as a graph can't carry on after a restart (their steps live in memory): they close with "run it
  again".
- The Windows CI hang can't be proved gone; the new stack dump and 40-minute stop are the safety net.
- An AI without a model card can't have its cost estimated before it runs.
- Own agents can use four tools only and live in the saved settings file (a hand-edited file can only lose power).
- Snapshots: elements inside iframes aren't included; `pyproject.toml` still says `playwright>=1.45`, older than the
  snapshot feature needs (an older one gives a plain "run pip install -U playwright" message); a page can still lie
  about its own buttons because the click rules read what the page reports.
- The audit was not an independent fresh session (see the exit gate).

## Merge
Not merged. Waiting for the owner to merge `prometheus-v3` after the laptop check above.
