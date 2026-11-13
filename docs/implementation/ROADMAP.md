# Prometheus 3.0: Implementation Roadmap

The one roadmap for Prometheus 3.0 (agreed with the owner, 13 Nov 2026). Details for M2 are in
[`docs/m2/M2_PLAN.md`](../m2/M2_PLAN.md). Scope is frozen until M10 is done: new ideas go in
[`docs/ideas-after-m10.md`](../ideas-after-m10.md).

| Milestone | Deliverables | Exit gate |
| :--- | :--- | :--- |
| **M0 Baseline** | Code inventory, architecture map, test baseline, test isolation. | Facts checked against the code. Done, on main. |
| **M1 Tool primitives** | Path hardening, atomic file edits, bounded reader/diff, async terminal, services wiring. | Tests pass on native Windows and Linux; two audits fixed. Done, on main. |
| **M2 Desktop interface** | New look, Home, chat, window (M2.1); pages for existing features (M2.2); projects, task runner, automation builder (M2.3); switch-over and removal of the old screens (M2.4). | Click-through test passes on Windows; every 2.x feature reachable; owner's sign-off. |
| **M3 Orchestration and model routing** | ReAct loop, default model per agent/task, smarter agent choice, streaming answers. | Routing and cancel tests pass. |
| **M4 Repository-to-skill** | The owner's repos read safely and mapped; useful parts become skills and searchable knowledge; sandboxed skill test-runs. | Skills made from repos validate and run in the sandbox. |
| **M5 Many agents** | DAG of tasks, concurrent child agents, your own agents, conversation branching. | Parallel and restart tests pass. |
| **M6 Runtime / Cloudroom** | ExecutionRuntime: LocalRuntime by default, Cloudroom optional for running tasks and free encrypted storage of Prometheus and its knowledge; Control PC. | Disconnect/reconnect tests; works offline. |
| **M7 Security hardening** | Per-project permissions, keys in Windows Credential Manager, safe-stop, adversarial tests. | No unapproved action in the test suite. |
| **M8 Memory engine** | Layered memory, hybrid retrieval, retrieval from repo knowledge (M4) and Cloudroom (M6). | Retrieval beats the current search. |
| **M9 Evaluation and release** | End-to-end tests, performance and accessibility audits, packaging. | Release gates met; limits written down. |
| **M10 Autonomy** | Automations and voice that act without asking (reserved until M0–M9 are done). | Owner's sign-off. |

---

*Policy: every milestone follows the [Global Open-Source Policy](../../AGENTS.md). Prometheus's own code is MIT
licensed, with no required closed-source dependencies.*
