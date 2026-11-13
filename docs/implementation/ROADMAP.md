# Prometheus 3.0: Implementation Roadmap

The one roadmap for Prometheus 3.0 (agreed with the owner, 13 Nov 2026). Details for M2 are in
[`docs/m2/M2_PLAN.md`](../m2/M2_PLAN.md). Scope is frozen until M10 is done: new ideas go in
[`docs/ideas-after-m10.md`](../ideas-after-m10.md).

| Milestone | Deliverables | Exit gate |
| :--- | :--- | :--- |
| **M0 Baseline** | Code inventory, architecture map, test baseline, test isolation. | Facts checked against the code. Done, on main. |
| **M1 Tool primitives** | Path hardening, atomic file edits, bounded reader/diff, async terminal, services wiring. | Tests pass on native Windows and Linux; two audits fixed. Done, on main. |
| **M2 Desktop interface** | New look, Home, chat, window (M2.1); pages for existing features (M2.2); projects, task runner, automation builder (M2.3); switch-over and removal of the old screens (M2.4). | Click-through test passes on Windows; every 2.x feature reachable; owner's sign-off. |
| **M3 Orchestration and model routing** | ReAct loop, default model per agent/task, smarter agent choice, streaming answers; **four routes (simple / medium / complex / ask-first) picked by a cheap, time-limited classifier with a plain-rules fallback; typed tool and agent registry with permission scopes; a small live-context line (time, due reminders, running tasks)** (F1–F3). | Routing and cancel tests pass. |
| **M4 Repository-to-skill** | The owner's repos read safely and mapped; useful parts become skills and searchable knowledge; sandboxed skill test-runs; **a per-project notes file (what the project is, decisions, changes) written safely with locks** (F8). | Skills made from repos validate and run in the sandbox. |
| **M5 Many agents** | DAG of tasks, concurrent child agents, your own agents, conversation branching; **every job tracked, with saved state, cancel and restart recovery, and a concurrency limit sized for the 8 GB laptop** (F4). | Parallel and restart tests pass. |
| **M6 Runtime / Cloudroom** | ExecutionRuntime: LocalRuntime by default, Cloudroom optional for running tasks and free encrypted storage of Prometheus and its knowledge; Control PC. | Disconnect/reconnect tests; works offline. |
| **M7 Security hardening** | Per-project permissions, keys in Windows Credential Manager, safe-stop, adversarial tests. | No unapproved action in the test suite. |
| **M8 Memory engine** | Layered memory, hybrid retrieval, retrieval from repo knowledge (M4) and Cloudroom (M6); **facts you state are protected, inferred ones carry a confidence that can fade; corrections become lessons; varied results (MMR); a size-limited context builder; every memory change visible and undoable** (F5–F7). | Retrieval beats the current search. |
| **M9 Evaluation and release** | End-to-end tests, performance and accessibility audits, packaging. | Release gates met; limits written down. |
| **M10 Autonomy** | Automations and voice that act without asking (reserved until M0–M9 are done). | Owner's sign-off. |

## Friday Mark 2 additions (owner decision, 13 Nov 2026)
The owner asked to compare the Friday Mark 2 research with our plan and add what is better. The **bold** items above
(F1–F8) are the result: improvements inside M3, M4, M5 and M8, not new milestones. The comparison, and what was
rejected, is in [`docs/reports/FRIDAY_MARK2_RESEARCH.md`](../reports/FRIDAY_MARK2_RESEARCH.md). No Friday code may be
copied (no licence found). Nothing is built yet; each item needs tests, a Windows run and the milestone audit.

---

*Policy: every milestone follows the [Global Open-Source Policy](../../AGENTS.md). Prometheus's own code is MIT
licensed, with no required closed-source dependencies.*
