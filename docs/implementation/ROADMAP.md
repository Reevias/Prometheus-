# Prometheus 3.0: Implementation Roadmap

| Milestone | Deliverables | Exit Gate |
| :--- | :--- | :--- |
| **M0 Baseline** | Code inventory, architecture map, test baseline, dependency matrix, risk register. | Facts verified against current commit. |
| **M1 Agent Core** | ReAct, typed dispatcher, exact file edit, window reader, PTY, budgets/cancel. | Tool tests and failure recovery pass. |
| **M2 Skills** | Manifest/registry, progressive disclosure, slash palette, /repo match/draft/validate. | Reuse and new-skill validation tested. |
| **M3 Orchestration** | Fast path, DAG, isolated child agents, checkpoints/reviewer. | Restart and parallel conflict tests pass. |
| **M4 Code Intelligence** | Tree-Sitter, symbol graph, repo profiles, /compact. | Large repo retrieval beats baseline. |
| **M5 Runtime** | ExecutionRuntime, LocalRuntime, Cloudroom adapter, events/recovery. | Disconnect/reconnect/idempotency tests pass. |
| **M6 Security** | Sandbox, secrets, approval policy, journal, STOP ALL, adversarial tests. | No unauthorized action in defined suite. |
| **M7 Memory & Sync** | Layered memory, hybrid retrieval, event replication/conflicts. | Offline replay/concurrent write tests pass. |
| **M8 Device & Voice** | Desktop accessibility, Android services, duplex voice and pairing. | Permissions and reconnect tests pass. |
| **M9 Release** | Benchmarks, telemetry, packaging, docs, migration/rollback. | Release gates met; limitations documented. |

---

*Mandatory Policy Note: Every milestone from M0 through M10 enforces full compliance with the [Global Open-Source Policy](../../AGENTS.md) as a non-negotiable exit criterion. All original source code remains open-source under the MIT License; no mandatory closed-source dependencies or proprietary runtime lock-in.*
