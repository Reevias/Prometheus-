# Prometheus 3.0: the plan (M0 to M10)

The one plan for Prometheus 3.0, approved by the owner on 13 Nov 2026. It replaces `docs/implementation/ROADMAP.md`,
`docs/architecture/PROMETHEUS_3_MASTER_PLAN.md` and the "later milestones" table in `docs/m2/M2_PLAN.md`. M2's
detailed steps stay in [`docs/m2/M2_PLAN.md`](m2/M2_PLAN.md). Built from the agreed M0–M10 roadmap, the M2 plan's "later milestones" table, the
Master Plan, the memory-chunking audit, the M2 known-limitations report, CLAUDE.md, AGENTS.md, and the Friday Mark 2
research PDF (13 Nov 2026), with the owner-approved ideas F1–F8 (comparison in [`docs/reports/FRIDAY_MARK2_RESEARCH.md`](reports/FRIDAY_MARK2_RESEARCH.md)).

Items marked **[F1]**…**[F8]** come from the Friday research. Items marked **[found]** were already written down
somewhere in the repo but missing from the roadmap table; they are placed here so nothing is lost.

## Rules for every milestone (unchanged)
- Windows first: tested on native Windows CI (`windows-latest`), say NOT VERIFIED when it couldn't be; never weaken
  Windows behaviour or security for another platform. 8 GB laptop, no GPU: free models first, paid only after the owner
  says yes, nothing over $1 in / $4 out per million tokens.
- A milestone starts only after the previous one is accepted and audited. Merge to main one milestone at a time, after
  its tests, a real Windows run and an audit. Logical commits; no force-push; no unapproved merges.
- Evidence over claims (code + tests + real runs + report); never fake telemetry, progress or results. Security is
  enforced in code, not prompts. Destructive actions need explicit approval. Residual risks written down honestly.
- Open-source policy (AGENTS.md): Prometheus code stays MIT; every new dependency goes through the 7-step licence
  review; open-source compliance is an exit check for every milestone.
- Friday Mark 2: ideas only, write our own code. No Friday code is copied (no licence found). Re-check its licence
  before using anything. It never becomes a dependency.
- Home stays minimal; colours #0B0B10 #17171F #211D1A #FF922B #FFC06A #F4F1ED #AAA5A0; LocalRuntime default, Cloudroom
  optional. Features hidden until their milestone, never shown as dead buttons. New Settings go in the Features list
  (one switch each) or Advanced.
- Scope: nothing beyond this plan until M10 is done. New ideas go in `docs/ideas-after-m10.md`.

## What Prometheus is (vision and design, from the Master Plan)
A personal AI operating system with a reliable agent harness at its core:
- **Assistant:** natural language and voice.
- **Agent harness:** dynamic execution, tools, task graphs and recovery.
- **Skills:** capability packages that show detail only when needed, used through slash commands.
- **Projects and memory:** lasting context, where each fact came from (provenance), continuity across sessions.
- **Device control:** desktop, browser and Android, with permission.
- **Where tasks run:** local first, Cloudroom optional.
- **Trust:** least privilege, approvals, audit, undo and a global STOP ALL.
- **Open source:** all original code M0–M10 stays MIT, with no required paid or closed services (AGENTS.md).

Design: near-black canvas `#0B0B10`, surfaces `#17171F`, raised `#211D1A`, orange `#FF922B`, amber `#FFC06A`, text
`#F4F1ED`, muted `#AAA5A0`; a glowing orange orbital core with subtle particles; a minimal Home (sidebar, orbital
mark, the Prometheus label, one command input); the slash palette appears only after typing `/` (no repo panels or
static examples on Home); status cards only when backed by real live readings.

The four basic tools of the agent engine: `edit_file_exact` (exact anchored edits, atomic write, diff check,
backup/journal) and `terminal_exec` (streaming terminal, cancel, clean-up of child processes) and `read_file_window`
(windowed reading, ripgrep/ast-grep search) were built in M1; Tree-Sitter symbol search comes in M4.2;
`browser_action` (Playwright/CDP, accessibility tree, snapshots/diffs) comes in M3.2.

## Where we are
| Milestone | Status |
|---|---|
| M0 Baseline (code inventory, architecture map, test baseline, test isolation) | Done, on main |
| M1 Tool primitives (path hardening, atomic file edits, bounded reader/diff, async terminal, services wiring) | Done, on main; two audits fixed |
| M2 Desktop interface (M2.1 look/Home/chat/window; M2.2 pages; M2.3 projects, task runner, automation builder; M2.4 switch-over) | In progress on `prometheus-v3`; details stay in `docs/m2/M2_PLAN.md` |

---

## M3 Orchestration and model routing
**Goal:** Prometheus picks the right path, agent, tools and model for each request, and shows answers as they arrive.

Phases:
- **M3.1 Router [F1]** — four routes: *simple* (fast path, plain rules, no big plan), *medium* (one agent),
  *complex* (full ReAct loop), *ask-first* (asks you a question when the request is unclear). A cheap, time-limited
  classifier picks the route; if it is slow or fails, plain rules decide. Rules alone never judge nuanced requests.
  (Master Plan "two paths" becomes four.)
- **M3.2 Tool and agent registry [F2]** — every tool and agent has a typed description (inputs, outputs, errors) and a
  permission scope. Keyword matching only makes a shortlist; the choice is then checked against capabilities and
  permissions. **[found]** `browser_action` (Playwright/CDP, accessibility tree, snapshots/diffs) from the Master
  Plan's four primitives is registered here as a tool; it was not in any milestone.
- **M3.3 ReAct loop** — think → act with a tool → observe → repeat, with step limits, cancel at any step, and visible
  steps (plans, tool calls, status, short explanations: never private chain-of-thought).
- **M3.4 Model routing** — a default model per agent and per task type; provider fallback when a model is down or
  out of quota (free first). Smarter automatic agent choice; `@` mention still overrides.
- **M3.5 Streaming answers** — words appear as they come (moved from M2.1 by the owner).
- **M3.6 Live context [F3]** — a small context line given to the model: time, due reminders, conflicts, running tasks.

Study (Friday, ideas only): `router/smart_router.py`, `router/llm_router.py`, `tools/registry.py`,
`agents/registry.py`, `awareness/live_context.py`.
**Exit gate:** routing tests (each route, classifier timeout → rules), cancel tests, streaming test, registry
validation tests; native Windows CI green; audit.

## M4 Repository-to-skill
**Goal:** turn useful parts of repositories, including the owner's own, into safe skills and searchable knowledge.

Phases:
- **M4.1 Safe read-only inspection** — read a repo without running it; map its files, stack and dependencies.
  Builds on M1's file tools.
- **M4.2 Code intelligence** — Tree-Sitter symbol maps (functions, classes, imports); `read_file_window` gains
  Tree-Sitter symbol search (Master Plan). **The owner's repos** (large, linked to each other): map how they connect.
- **M4.3 Repo hub** — a separate place where candidate repos and skills wait before they are accepted (CLAUDE.md).
- **M4.4 Skills from repos** — match an existing skill or draft a new least-privilege one with `SKILL.md` and a
  manifest; dependency checks (licence review per AGENTS.md); stable slash commands.
- **M4.5 Sandboxed test-runs** — every new skill runs in the sandbox before it can be enabled.
- **M4.6 Searchable repo knowledge** — useful parts become knowledge M8 can search.
- **M4.7 Project notes file [F8]** — one notes file per project (what it is, decisions, changes), written safely with
  locks and M1's atomic writes.

Study: `memory/project_chronicle/`.
**Exit gate:** skills made from repos validate and run in the sandbox; repo map tests; Windows CI; audit.

## M5 Many agents
**Goal:** several agents work at once on a graph of tasks, safely, and survive restarts.

Phases:
- **M5.1 Job tracking [F4]** — every job has a saved state (pending, running, paused, done, failed, cancelled), can be
  cancelled, and recovers after a restart or crash. No untracked background tasks. (Today only tasks paused by an AI
  limit resume.)
- **M5.2 Task graph (DAG)** — dependencies, parallel and sequential steps, retries, cancel spreads to children,
  results passed along.
- **M5.3 Concurrent child agents** — a concurrency limit sized for the 8 GB laptop; lifecycle (start, status, clean up).
- **M5.4 Your own agents** — create, edit and remove your own agents.
- **M5.5 Full multi-agent view** — replaces M2's simple "running now" list.
- **M5.6 Conversation branching.**

Study: `execution/engine.py`, `parallel_executor.py`, `series_executor.py`, `state_manager.py`,
`subagent_registry.py`, `memory_aware_executor.py`.
**Exit gate:** parallel, cancel and restart tests pass; memory use stays within the laptop limit; Windows CI; audit.

## M6 Runtime / Cloudroom
**Goal:** tasks can run locally (default) or in Cloudroom (optional), and Prometheus can be stored there for free.

Phases:
- **M6.1 ExecutionRuntime** — one provider-neutral way to run tasks; `LocalRuntime` is the default.
- **M6.2 CloudroomRuntime (optional)** — isolated cloud sandboxes; live thread teleportation (move a running task);
  session receipts, request IDs, ordered event streaming, failure recovery (Master Plan).
- **M6.3 Cloudroom storage** — free storage of Prometheus and its knowledge (repo knowledge, backups, sync).
  Encrypted on the laptop before upload; the laptop copy stays the main one; works offline if Cloudroom is down.
- **M6.4 Control PC** (hidden in M2 until now).

Study: Friday's provider abstraction, session state and recovery patterns.
**Exit gate:** disconnect/reconnect tests; everything works offline; encryption tests; Windows CI; audit.

## M7 Security hardening
**Goal:** nothing happens without permission, and everything can be stopped.

Phases:
- **M7.1 Per-project permissions** — full, per project; tool scopes from M3.2 enforced here.
- **M7.2 Keys in Windows Credential Manager.**
- **M7.3 Safe-stop** — global STOP ALL that really stops every agent, job and runtime; approvals, audit log and undo
  (Master Plan "Trust").
- **M7.4 Own threat model** — written for Prometheus (Friday's privacy boundaries and circuit breakers are ideas only).
- **M7.5 Adversarial tests** — attempts to trick Prometheus into unapproved actions.

**Exit gate:** no unapproved action in the test suite; safe-stop tests; Windows CI; audit.

## M8 Memory engine
**Goal:** Prometheus remembers the right things, finds them well, and you stay in control.

Phases:
- **M8.1 Layers** — short-term, conversation, project, long-term knowledge, and your profile; project and
  conversation memory; project-separated namespaces. Evolve the existing `memory/` code; no second memory system.
- **M8.2 Facts and confidence [F5]** — facts you state are protected and never fade; inferred ones carry a confidence
  that can rise or fade. Every memory change is visible and undoable.
- **M8.3 Learning from corrections [F6]** — corrections become lessons, kept apart as facts, preferences and task
  lessons; safeguards checked first.
- **M8.4 Better search [F7]** — hybrid search (words + meaning) with varied results (MMR); a size-limited context
  builder; benchmarked against today's search.
- **M8.5 Chunking upgrades [found, memory audit]** — `project_id` on documents and chunks (with index); code split at
  functions/classes using M4's Tree-Sitter; heading path stored per chunk; a hash per chunk so only changed parts are
  re-embedded; `prometheus memory reindex` when the embedding model changes.
- **M8.6 Fully automatic memory and memory import** (M2 keeps instant "remember…" until then).
- **M8.7 Searching repo knowledge (M4) and Cloudroom storage (M6)**; syncing memory changes between copies.

Decisions needed from the owner before M8 starts [found, memory audit]: projects split by folder or by an ID you
choose; a small built-in embedding model or keep using local Ollama; how to choose the code vs text splitter.
Study: `memory/pipeline.py`, `context_assembler.py`, `background/knowledge_extractor.py`, `memory_decay.py`,
`reasoning/reflection.py`. Benchmark SQLite connection/transaction handling ourselves.
**Exit gate:** retrieval beats the current search on a fixed test set; stated facts never lost; Windows CI; audit.

## M9 Evaluation and release
**Goal:** prove it all works together, then ship.

Phases:
- **M9.1 End-to-end, integration and stress tests**; health checks.
- **M9.2 Performance** — automatic performance tuning audit on the 8 GB laptop.
- **M9.3 Accessibility audit.**
- **M9.4 Diagnostics polish** — including GPU load on integrated graphics via DirectX/DXGI counters **[found, M2
  known limitations]** (shows N/A today).
- **M9.5 Final visual sign-off.**
- **M9.6 Packaging** — Setup.exe and APK, SBOM and install manifest (AGENTS.md); limits written down.

**Exit gate:** release gates met; limits written down; owner tries it on the laptop.

## M10 Autonomy (reserved until M0–M9 are done)
**Goal:** Jarvis-style help (reference: hectorg2211/jarvis) that can act without asking, within the M7 rules.
- **M10.1** Automations that act without asking (built on reminders, watchers and the M2.3 builder).
- **M10.2** Voice that acts on its own.
- **M10.3** Background work: proactive watchers, memory upkeep. Study Friday's scheduler and goal-tracker ideas.

**Exit gate:** owner's sign-off.

---

## Check: everything from the old plan is here
| Old item (source) | New place |
|---|---|
| ReAct loop (roadmap) | M3.3 |
| Default model per agent/task (roadmap, M2 plan) | M3.4 |
| Smarter agent choice (roadmap, M2 plan) | M3.2, M3.4 |
| Streaming answers (roadmap, M2 plan) | M3.5 |
| Two paths: fast + ReAct (Master Plan §3) | M3.1 (now four routes) |
| `browser_action` primitive (Master Plan §3) | M3.2 [found] |
| Owner's repos read safely and mapped (roadmap, M2 plan) | M4.1, M4.2 |
| Useful parts → skills and searchable knowledge (roadmap) | M4.4, M4.6 |
| Sandboxed skill test-runs (roadmap, M2 plan) | M4.5 |
| Dependency checks (M2 plan) | M4.4 |
| Tree-Sitter mapping, stack profiling, SKILL.md, manifests, slash commands (Master Plan §4) | M4.2, M4.4 |
| Separate repo hub for candidates (CLAUDE.md) | M4.3 |
| DAG, concurrent child agents (roadmap) | M5.2, M5.3 |
| Your own agents, full multi-agent view, branching (roadmap, M2 plan) | M5.4–M5.6 |
| ExecutionRuntime, LocalRuntime default, Cloudroom optional (roadmap, Master Plan §5) | M6.1, M6.2 |
| Teleportation, receipts, request IDs, event streaming, recovery (Master Plan §5) | M6.2 |
| Cloudroom free encrypted storage, offline, laptop copy main (roadmap, M2 plan) | M6.3 |
| Control PC (roadmap, M2 plan) | M6.4 |
| Per-project permissions, Credential Manager (roadmap, M2 plan) | M7.1, M7.2 |
| Safe-stop, adversarial tests (roadmap, CLAUDE.md) | M7.3, M7.5 |
| Approvals, audit, undo, STOP ALL (Master Plan §1) | M7.3 |
| Layered memory, hybrid retrieval (roadmap) | M8.1, M8.4 |
| Project and conversation memory, long-term knowledge, automatic memory, import (M2 plan) | M8.1, M8.6 |
| Retrieval from repo knowledge and Cloudroom (roadmap, M2 plan) | M8.7 |
| Multi-layer memory, project namespaces, vector migration, event replication (memory audit §18) | M8.1, M8.5, M8.7 |
| Memory audit §17 items 1–5 and §19 open questions | M8.5, "Decisions needed" |
| End-to-end tests, performance and accessibility audits, packaging (roadmap, M2 plan) | M9.1–M9.3, M9.6 |
| Diagnostics polish, final visual sign-off (M2 plan) | M9.4, M9.5 |
| iGPU load via DXGI ("M4+", M2 known limitations) | M9.4 [found] |
| SBOM and manifests (AGENTS.md) | M9.6 |
| Automations and voice that act without asking (roadmap, M2 plan) | M10.1, M10.2 |
| Jarvis reference, reserved (CLAUDE.md) | M10 |
| Master Plan vision, design, colours, minimal Home, slash palette, real telemetry (§1, §2) | "What Prometheus is" |
| `edit_file_exact`, `terminal_exec`, `read_file_window` (Master Plan §3) | "What Prometheus is" (built in M1) |
| M0, M1, M2 rows (roadmap) | "Where we are" |
| Exit gates (roadmap) | each milestone, kept and made more specific |
| Open-source policy note (roadmap) | Rules |
| Scope freeze, ideas file (roadmap, M2 plan) | Rules |
| F1–F8 (Friday comparison) | M3.1, M3.2, M3.6, M5.1, M8.2, M8.3, M8.4, M4.7 |
| Friday study files and "don't copy blindly" rules | "Study" lines and Rules; full table stays in `docs/reports/FRIDAY_MARK2_RESEARCH.md` |
