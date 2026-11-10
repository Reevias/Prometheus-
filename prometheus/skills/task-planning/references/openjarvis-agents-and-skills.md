# OpenJarvis: agents, skills, learning, security

Source: https://github.com/open-jarvis/OpenJarvis (Apache-2.0, as of 2026-11-09)

Files read: `docs/architecture/{agents,skills,security,learning}.md`, `docs/user-guide/skills.md`, `src/openjarvis/agents/loop_guard.py`, `src/openjarvis/skills/security.py`, `src/openjarvis/security/*.py` headers.

## Agent types (pick the simplest that works)

| Key | Style | Use when |
|---|---|---|
| `simple` | One model call, no tools | Plain Q&A |
| `orchestrator` | Tool-calling loop; stops when no tool is requested; `max_turns` default 10 | Default for tool use |
| `native_react` | Thought, Action, Action Input, Observation until "Final Answer" | You want visible reasoning |
| `native_openhands` | Writes and runs Python (CodeAct), also tools | Code tasks |
| `rlm` | Keeps long text in a REPL variable; sub-model calls (temperature 0.3, 1024 tokens) | Text too long for the prompt |
| `sandboxed` | Wraps any agent in a container, no network | Untrusted input |
| `operative` | Scheduled, keeps state between runs | Recurring jobs |
| deep research agent | Multi-hop search of personal data with inline citations | Questions over your own data |

A research loop (`research_loop.py`) is a small planner with one search tool and `max_iterations` 5.

**Loop guard** (`agents/loop_guard.py`): blocks after 3 identical tool calls (hash of name and args), spots A-B-A-B cycles in a window of 6, limits polling tools to 5 calls, warns before blocking, and compresses history on overflow.

## Skills

A skill is a folder with `skill.toml` (a fixed pipeline), `SKILL.md` (instructions), or both.

- Pipeline steps: `tool_name` (or `skill_name`), `arguments_template` with `{placeholders}`, `output_key` to feed later steps. Example `calendar-prep`: think, then memory search, then think to write the briefing.
- Instruction-only skills return markdown that the agent follows with its own tools.
- Manifest fields: name, version, description, steps, `required_capabilities`, signature, tags, `depends`.
- The agent sees a light catalog (name and description) and loads a skill on use, like Prometheus.
- Name precedence: workspace, then user, then bundled.
- Skills may depend on other skills; discovery checks for cycles and depth (default max 5).
- Imported skills get tool names translated, and a `.source` provenance file.

## Learning loop

1. Run the agent; a collector records a trace (steps, tools, latency).
2. Store traces; analyse success rate and latency per model and tool.
3. A trace-driven router learns which model suits each query class; it trusts the learning only after `min_samples` (default 5), else uses simple rules (code goes to a coder model, short goes to a small model, long or reasoning goes to a large one, urgent goes to the fastest).
4. Reward score default: 0.4 latency, 0.3 cost, 0.3 efficiency.
5. `skill discover` mines traces for repeated tool sequences (`--min-frequency 3`) and writes candidate skills.
6. Edits carry risk tiers: auto (model or tool settings), review (prompt edits, queued for approval), manual (never auto).

## Security layers

| Layer | What it does |
|---|---|
| Secret and PII scanners | Modes WARN, REDACT, BLOCK on input and output |
| Boundary guard | Scans data before it leaves the device to cloud or external tools |
| Injection scanner | Regex patterns for prompt injection, with threat levels |
| File policy | Sensitive file paths are never read or indexed |
| SSRF guard | Blocks private IPs and cloud metadata addresses |
| Capabilities (RBAC) | Per-tool permissions such as `file:read`, `network:fetch`, `code:execute` |
| Taint tracking | Labels data PII, SECRET, USER_PRIVATE, EXTERNAL to stop leaks along tool chains |
| Skill trust tiers | bundled, workspace, indexed (signed), unreviewed; dangerous capabilities flagged |
| Rate limiter, subprocess sandbox, signing, audit log (SQLite) | Throttling, isolation, Ed25519 manifests, traceability |

## Not verified

- Regex lists inside the scanners.
- Rust acceleration details and DSPy/GEPA optimizer internals.
- Hybrid and channel agents were not read.
