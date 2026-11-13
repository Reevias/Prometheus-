# Friday Mark 2: research and roadmap mapping (13 Nov 2026)

Source: the owner's report "New changes and betterment of features" (PDF, 6 pages). Repo studied:
`github.com/pranithvelar/Friday_mark2` (main branch, tree commit `45b72243ed775d9fbb2b6ee09b059518ab5ed4e8`).

**Status: research only. This does not change the M0–M10 plan, add deliverables or authorise any work.** Nothing here
is a task. Scope stays frozen until M10 (see `docs/implementation/ROADMAP.md`).

## What it is (and isn't)
A reference project for orchestration, tool/agent registries, parallel execution, layered memory and background work.
Overall: 8.5/10 as a *reference* (how relevant the ideas are), **not** a code-quality or security rating. The review
read selected files only: not a line-by-line or security audit. Its own README lists unfinished parts (researcher agent,
Telegram, authenticated API, LiveKit voice, Google Calendar/Gmail OAuth, MCP, a pytest suite).

## Licence: direct code reuse NOT approved
No root LICENSE file and no licence in `pyproject.toml`. A public repo is not automatically free to reuse.
**Rule:** study the ideas and write our own code. Do not copy or include Friday source unless the author gives an explicit
licence or permission. Prometheus stays MIT; every dependency's licence is checked separately.
Friday must never become a dependency, and Prometheus must not copy its architecture wholesale.

## Twelve areas worth studying
| # | Area | What it offers | Milestone | Value |
|---|---|---|---|---|
| 1 | Four-tier router | simple / medium / complex / clarify, bounded LLM classification with a fixed fallback | M3 | High |
| 2 | Tool registry | discovery, schemas, dispatch, scope rules | M3/M5 | High |
| 3 | Agent registry | agent discovery and capability matching (theirs is keyword-based) | M3/M5 | High |
| 4 | Six-layer memory | short-term, episodic, semantic, procedural, profile | M8 | Very high |
| 5 | Hybrid retrieval / RAG | embeddings, cache, hybrid search, MMR; compare with our SQLite/FTS/vector first | M8 | High |
| 6 | Reflection / learning | learning from corrections; facts vs preferences vs task lessons | M8 | High |
| 7 | Parallel / sequential execution | executors, state manager, subagent registry | M5 | Very high |
| 8 | Live context awareness | time, itinerary, conflicts, reminders, status in compact form | M3/M8 | High |
| 9 | Project Chronicle | project classification, project logs, locks, safe writes | M4/M8 | Very high |
| 10 | Memory promotion / decay | confidence lifecycle; explicit facts protected | M8 | High |
| 11 | Background scheduler | watchers, maintenance, proactive events | M10 | High |
| 12 | Voice / external interfaces | interface separation (LiveKit, messaging, API are stubs there) | M2/M10 | Partial |

## Mapping to our milestones (relevance for study, not scope)
M0 low · **M1** medium, looking back only (M1 is closed; no new work) · **M2** medium (truthful status data) ·
**M3** very high · **M4** very high · **M5** very high · **M6** high · **M7** medium (ideas only; we need our own
threat model) · **M8** very high · **M9** medium (their test maturity is not assumed) · **M10** very high.
Strongest matches: M3, M4, M5, M8, M10.

## Files to read when each milestone starts
| Friday file | Purpose | Milestone |
|---|---|---|
| `friday/router/smart_router.py` | central routing, memory write path, context assembly | M3 |
| `friday/router/llm_router.py` | LLM classification, timeout, regex fallback | M3 |
| `friday/tools/registry.py` | tool discovery, schemas, dispatch | M3 |
| `friday/agents/registry.py` | agent discovery, capability lookup | M3/M5 |
| `friday/execution/engine.py` | task execution structure | M5 |
| `friday/execution/parallel_executor.py` | parallel pattern | M5 |
| `friday/execution/state_manager.py` | execution lifecycle, persistence | M5 |
| `friday/execution/memory_aware_executor.py` | execution with memory context | M5/M8 |
| `friday/memory/pipeline.py` | background memory ingestion | M8 |
| `friday/memory/context_assembler.py` | context selection | M8 |
| `friday/background/knowledge_extractor.py` | fact and pattern extraction | M8 |
| `friday/background/memory_decay.py` | decay and protection of explicit facts | M8 |
| `friday/reasoning/reflection.py` | reflection; check safeguards before reuse | M8 |
| `friday/awareness/live_context.py` | live context state and refresh | M3/M8 |
| `friday/background/scheduler.py` | scheduler interface | M10 |
| `friday/memory/project_chronicle/` | project classification and logging | M4/M8 |

Research order: M3 → M5 → M8 → M4 → M6/M7/M9/M10 (only when those milestones are reached).

## What not to copy blindly
| Area | Risk | Our rule |
|---|---|---|
| Private thought traces | stores chain-of-thought | show plans, tool calls, status, short explanations: not private reasoning |
| Regex-only routing | misses nuanced intent | regex is a fallback, never the only judge |
| Fire-and-forget jobs | untracked asyncio tasks get lost at shutdown | tracked jobs, durable state, cancel and recovery |
| One shared SQLite connection | bottleneck under concurrency | benchmark; design connection/transaction handling ourselves |
| Keyword agent matching | too simple | use only to shortlist; validate the choice and permissions |
| Memory decay | can hide useful inferred memories | protect explicit facts; user controls; auditable lifecycle |
| Unverified security | descriptions and passing tests are not proof | threat-model and test our own versions |
| Unfinished interfaces | stubs / roadmap items | never claim they are ready to reuse |

## Skills the plan needs (from the report)
Async Python and task lifecycle (M3/M5) · LLM intent classification (M3) · tool schemas and dispatch (M3) · agent
lifecycle (M5) · DAG scheduling and concurrency (M5) · durable background jobs (M5/M10) · SQLite and retrieval
engineering (M8) · knowledge extraction and confidence (M8) · repo parsing and code intelligence (M4) · provider
abstraction (M3/M6) · security engineering (M7) · evaluation engineering (M9).

## Final assessment
Architecture ideas: excellent reference · routing: very useful · memory concepts: excellent · multi-agent execution:
very useful · project knowledge: very useful · security assurance: not established · test maturity: not established ·
direct code reuse: **not approved until licensed**. Next step (only when authorised): compare Friday's strongest M3, M5
and M8 parts against Prometheus's own source.

Re-check Friday's branch head and licence before any implementation decision.
