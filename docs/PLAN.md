# Prometheus 3.0: the plan (M0 to M10)

The one plan for Prometheus 3.0, approved by the owner (13 Nov 2026), status updated 14 Nov 2026.
M2's detailed steps: [`docs/m2/M2_PLAN.md`](m2/M2_PLAN.md). Friday Mark 2 comparison:
[`docs/reports/FRIDAY_MARK2_RESEARCH.md`](reports/FRIDAY_MARK2_RESEARCH.md).

**11 milestones, 59 steps.** Done: M0, M1 (10 steps). In progress: M2 (2 of 5 steps built, M2.2 started).
Not started: M3 to M10.

Status words: **Done** = built, tested, audited and accepted · **Built** = code and tests are in, waiting for the
owner's laptop check and the audit · **In progress** · **Not started**.
Labels: **[F1]–[F8]** = better ideas from the Friday Mark 2 research (ideas only, no code copied) · **[found]** =
already written down elsewhere in the repo, missing from the old roadmap.

## Overview
| Milestone | Steps | Status |
|---|---|---|
| M0 Baseline | 5 | Done (on main) |
| M1 Tool primitives | 5 | Done (on main) |
| M2 Desktop interface | 5 | In progress (on `prometheus-v3`) |
| M3 Orchestration and model routing | 6 | Not started |
| M4 Repository-to-skill | 7 | Not started |
| M5 Many agents | 6 | Not started |
| M6 Runtime / Cloudroom | 4 | Not started |
| M7 Security hardening | 5 | Not started |
| M8 Memory engine | 7 | Not started |
| M9 Evaluation and release | 6 | Not started |
| M10 Autonomy | 3 | Reserved until M0–M9 are done |
| **Total** | **59** | |

## Locked rules (every milestone)
1. **Windows first.** Tested on native Windows CI (GitHub's Windows machines, not the owner's laptop); if it couldn't
   be tested on Windows it is marked NOT VERIFIED. Security and Windows behaviour are never weakened for another
   platform.
2. **The laptop:** 8 GB RAM, no GPU. Free AI models first; paid only after the owner says yes; never over $1 in /
   $4 out per million tokens.
3. **One milestone at a time.** It starts only after the previous one is accepted and audited, and is merged into
   main on its own after its tests, a real Windows run and an audit. Logical commits; no force-push; no unapproved
   merges.
4. **Evidence over claims:** code, tests, real runs and a report. Telemetry, progress and results are never faked.
5. **Safety:** security enforced in code, not prompts; destructive actions need the owner's approval; leftover risks
   written down honestly.
6. **Open source:** Prometheus code stays MIT; every new dependency passes the 7-step licence check in `AGENTS.md`;
   open-source compliance is an exit check of every milestone.
7. **Friday Mark 2:** study the ideas, write our own code. No Friday code is copied (no licence found); re-check its
   licence before using anything; never a dependency.
8. **Design:** colours `#0B0B10` `#17171F` `#211D1A` `#FF922B` `#FFC06A` `#F4F1ED` `#AAA5A0`; Home stays minimal; a
   feature stays hidden until its milestone (no dead buttons); status shows real readings only.
9. **Settings:** new things go in the Features list (one switch each); technical knobs under Advanced.
10. **Stack stays:** Python, FastAPI, Jinja2, HTMX, Canvas, PyWebView.
11. **Scope freeze:** nothing beyond this plan until M10 is done; new ideas go in `docs/ideas-after-m10.md`.

## Vision
A personal AI operating system with a reliable agent engine at its core:
- **Assistant:** text and voice.
- **Agent engine:** tools, task graphs and recovery.
- **Skills:** packages used through slash commands, showing detail only when needed.
- **Projects and memory:** context that lasts across sessions and remembers where each fact came from.
- **Device control:** desktop, browser and Android, only with permission.
- **Where tasks run:** the laptop first; Cloudroom (cloud) optional.
- **Trust:** least privilege, approvals, audit log, undo and a global STOP ALL.

---

## M0 Baseline — Done (on main) · 5 steps
| Step | What | Status |
|---|---|---|
| M0.1 | Code inventory (`docs/m0/CODE_INVENTORY.md`) | Done |
| M0.2 | Architecture map (`docs/m0/ARCHITECTURE_MAP.md`) | Done |
| M0.3 | Dependency and licence matrix (`docs/m0/DEPENDENCY_MATRIX.md`) | Done |
| M0.4 | Risk register (`docs/m0/RISK_REGISTER.md`) | Done |
| M0.5 | Test baseline and test isolation (`docs/m0/TEST_BASELINE.md`) | Done |

Exit gate: every fact checked against the code. Met.

## M1 Tool primitives — Done (on main) · 5 steps
| Step | What | Status |
|---|---|---|
| M1.1 | Path hardening | Done |
| M1.2 | Atomic file edits (`edit_file_exact`: exact edits, diff check, backup and journal) | Done |
| M1.3 | Bounded reader and diff (`read_file_window`) | Done |
| M1.4 | Async terminal (`terminal_exec`: streaming, cancel, cleans up child processes) | Done |
| M1.5 | Services wiring | Done |

Exit gate: two audits fixed (5 + 16 findings, `docs/reports/PROMETHEUS_M1_FINDINGS_FIX_REPORT.md`); tests pass on
Windows and Linux. Met.

## M2 Desktop interface — In progress (on `prometheus-v3`) · 5 steps
| Step | What | Status |
|---|---|---|
| M2.0 | Groundwork: main brought into v3; the first M2's made-up values replaced by real ones; roadmap fixed (now this file); click-through test of every control on Windows | Done |
| M2.1 | Look, Home, chat, window: colours, sidebar, orbital sphere, command bar, `/` and Ctrl+K palettes, attachments, chats (highlighting, tables, maths, diagrams, export), quick actions, right panel with real readings, mic and waveform, Settings in the new look, window that remembers its place and hides to the tray | Built (waiting for the owner's laptop check and the audit) |
| M2.2 | Pages for what already exists: Agents (built: real status, on/off per agent, `@agent`), Skills, Tasks, Memory (with file search and privacy), Automations, System (with Activity tab), Research (three modes: Research, Study, Money), chat files panel; Approvals, Phone and Connections in the profile menu; Costs in AI models and keys; old addresses redirected. Sidebar: 9 links + gear (details in `docs/m2/M2_PLAN.md`, "M2.2 decisions") | In progress |
| M2.3 | New basics: Projects, task runner (pause/resume, Kanban, timeline), automation builder | Not started |
| M2.4 | Switch-over: new screens become the only ones, old ones deleted, audit, merge into main, release | Not started |

Exit gate: click-through test passes on Windows; every 2.x feature reachable; M2 audit; the owner's sign-off.

## M3 Orchestration and model routing — Not started · 6 steps
- **M3.1 Router [F1].** Four routes: *simple* (fast path, plain rules), *medium* (one agent), *complex* (full ReAct
  loop), *ask-first* (asks a question when the request is unclear). A cheap, time-limited classifier picks the route;
  plain rules decide if it is slow or fails, but rules alone never judge nuanced requests.
- **M3.2 Tool and agent registry [F2].** Every tool has typed inputs, outputs and errors plus a permission scope.
  Keywords only make a shortlist; the choice is checked against capabilities and permissions. Registers
  `browser_action` (Playwright browser control, accessibility tree, snapshots) **[found]**.
- **M3.3 ReAct loop.** Think → act → observe, with step limits, cancel at any step and visible steps (plans, tool
  calls, status, short explanations; never hidden reasoning).
- **M3.4 Model routing.** Default model per agent and task type; fallback when a model is down or out of quota (free
  first); smarter agent choice; `@` mention still overrides.
- **M3.5 Streaming answers.** Words appear as they come (moved from M2.1).
- **M3.6 Live context [F3].** A small line for the model: time, due reminders, conflicts, running tasks.

Study (Friday, ideas only): `router/smart_router.py`, `router/llm_router.py`, `tools/registry.py`,
`agents/registry.py`, `awareness/live_context.py`.
Exit gate: routing (each route, classifier timeout → rules), cancel, streaming and registry tests; Windows CI; audit.

## M4 Repository-to-skill — Not started · 7 steps
- **M4.1** Read repos safely without running them; map files, stack and dependencies (builds on M1's file tools).
- **M4.2** Tree-Sitter code maps (functions, classes, imports) and symbol search in `read_file_window`; map how the
  owner's large, linked repos connect.
- **M4.3** Repo hub: candidate repos and skills wait here until accepted.
- **M4.4** Skills from repos: match an existing skill or draft a least-privilege one with `SKILL.md` and a manifest;
  dependency and licence checks; stable slash commands.
- **M4.5** Every new skill test-runs in the sandbox before it can be enabled.
- **M4.6** Useful repo parts become searchable knowledge (used by M8).
- **M4.7** A notes file per project (what it is, decisions, changes), written safely with locks and M1's atomic
  writes **[F8]**.

Study: `memory/project_chronicle/`.
Exit gate: skills made from repos validate and run in the sandbox; repo-map tests; Windows CI; audit.

## M5 Many agents — Not started · 6 steps
- **M5.1** Every job tracked: saved state (pending, running, paused, done, failed, cancelled), cancel, recovery after
  a restart or crash; no untracked background tasks **[F4]**.
- **M5.2** Task graph (DAG): dependencies, parallel and step-by-step tasks, retries, cancel spreads to children.
- **M5.3** Child agents running at once, with a limit sized for the 8 GB laptop; start, status, clean-up.
- **M5.4** Create, edit and remove your own agents.
- **M5.5** Full multi-agent view (replaces M2's simple "running now" list).
- **M5.6** Conversation branching.

Study: `execution/engine.py`, `parallel_executor.py`, `series_executor.py`, `state_manager.py`,
`subagent_registry.py`, `memory_aware_executor.py`.
Exit gate: parallel, cancel and restart tests; memory stays within the laptop's limit; Windows CI; audit.

## M6 Runtime / Cloudroom — Not started · 4 steps
- **M6.1** ExecutionRuntime: one way to run tasks; LocalRuntime (the laptop) is the default.
- **M6.2** Optional Cloudroom sandboxes: move a running task, receipts and request IDs, events in order, recovery
  after failures.
- **M6.3** Free Cloudroom storage of Prometheus and its knowledge (repo knowledge, backups, sync): encrypted on the
  laptop before upload; the laptop copy stays the main one; works offline.
- **M6.4** Control PC.

Exit gate: disconnect/reconnect tests; works offline; encryption tests; Windows CI; audit.

## M7 Security hardening — Not started · 5 steps
- **M7.1** Full per-project permissions, enforcing the tool scopes from M3.2.
- **M7.2** API keys in Windows Credential Manager.
- **M7.3** Safe-stop: a STOP ALL that really stops every agent, job and runtime; approvals, audit log, undo.
- **M7.4** Prometheus's own threat model (Friday's privacy boundaries and circuit breakers are ideas only).
- **M7.5** Adversarial tests: attempts to trick Prometheus into unapproved actions.

Exit gate: no unapproved action in the test suite; safe-stop tests; Windows CI; audit.

## M8 Memory engine — Not started · 7 steps
- **M8.1** Memory layers: short-term, conversation, project, long-term and your profile, separated per project.
  Built on the existing memory code; no second memory system.
- **M8.2** Facts you state never fade; inferred ones carry a confidence that can rise or fade; every change visible
  and undoable **[F5]**.
- **M8.3** Corrections become lessons, kept apart as facts, preferences and task lessons; safeguards checked first
  **[F6]**.
- **M8.4** Hybrid search (words plus meaning) with varied results (MMR) and a size-limited context builder,
  benchmarked against today's search **[F7]**.
- **M8.5** Chunking upgrades **[found]**: a project ID on documents and chunks; code split at functions and classes
  (M4's Tree-Sitter); heading path per chunk; a hash per chunk so only changed parts are re-embedded;
  `prometheus memory reindex` when the embedding model changes.
- **M8.6** Fully automatic memory and memory import (instant "remember…" stays until then).
- **M8.7** Search across repo knowledge (M4) and Cloudroom (M6); sync memory changes between copies.

Owner decides before M8 starts **[found]**: projects split by folder or by an ID you choose; a small built-in
embedding model or keep using Ollama; how the code or text splitter is chosen.
Study: `memory/pipeline.py`, `context_assembler.py`, `background/knowledge_extractor.py`, `memory_decay.py`,
`reasoning/reflection.py`. Benchmark SQLite connection and transaction handling ourselves.
Exit gate: search beats today's search on a fixed test set; stated facts never lost; Windows CI; audit.

## M9 Evaluation and release — Not started · 6 steps
- **M9.1** End-to-end, integration and stress tests; health checks.
- **M9.2** Performance tuning on the 8 GB laptop.
- **M9.3** Accessibility audit.
- **M9.4** Diagnostics polish, including GPU load on built-in graphics via DirectX/DXGI (N/A today) **[found]**.
- **M9.5** Final visual sign-off.
- **M9.6** Packaging: Setup.exe, APK, a list of every component and its licence (SBOM), install manifest, known
  limits written down.

Exit gate: release checks met; limits written down; the owner tries it on the laptop.

## M10 Autonomy (Jarvis-style, reference hectorg2211/jarvis) — Reserved · 3 steps
- **M10.1** Automations that act without asking, within the M7 rules (built on reminders, watchers and the M2.3
  builder).
- **M10.2** Voice that acts on its own.
- **M10.3** Background work: proactive watchers and memory upkeep (study Friday's scheduler and goal-tracker ideas).

Exit gate: the owner's sign-off.

---

## What we decided not to take from Friday Mark 2
Private thought traces, regex-only routing, fire-and-forget background tasks, one shared SQLite connection,
keyword-only agent matching, and any Friday source code (no licence). Its scheduler and voice interfaces are not
better than ours; M10 stays as planned.

## History of this plan
- 12 Nov 2026: owner's roadmap PDF; M0–M10 agreed.
- 13 Nov 2026: scope frozen; owner's repos (M4), Cloudroom storage (M6), retrieval (M8) were the last additions.
- 13 Nov 2026: Friday Mark 2 research compared with the plan; owner approved F1–F8.
- 13 Nov 2026: this file replaced `docs/implementation/ROADMAP.md`, `docs/architecture/PROMETHEUS_3_MASTER_PLAN.md`
  and the M2 "later milestones" table, after every item was checked to be here.
- 14 Nov 2026: status marked per step; M0–M2 split into steps.
