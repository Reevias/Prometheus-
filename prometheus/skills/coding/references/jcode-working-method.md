# jcode: working method

Source: https://github.com/1jehuang/jcode (MIT, as of 2026-11-09)
Files read: README.md, docs/HARNESS.md, docs/WRAPPERS.md, docs/RESUME_BEHAVIOR.md, docs/MEMORY_ARCHITECTURE.md, docs/SOFT_INTERRUPT.md, crates/jcode-app-core/src/tool/ (file names), tool/bash_destructive_gate.rs, src/cli/args.rs.

## How the harness works

jcode runs a background server (daemon) and clients connect to it. The agent loop sends your message plus tool definitions to the model, runs the tools the model asks for, and feeds results back.

Built-in tools (from the tool folder): `bash`, `read`, `write`, `edit`, `apply_patch`, `agentgrep` (search), `glob`, `grep`, `webfetch`, `websearch`, `memory`, `todo`/goal planning, `subagent`, `swarm`/communicate, `browser`, `mcp`, `batch`, `bg` (background tasks), `selfdev`. You can limit tools: `--tools bash,read,write,apply_patch`, `--tools minimal`, or hide some with an exclude list. A smaller tool set means a shorter prompt, which helps small models.

## Token saving

- `agentgrep` returns matches with file structure (function names and places), so the agent need not read whole files, and it trims results it has already shown.
- Provider prompt caching: jcode watches cache usage and reports cache misses (docs/HARNESS.md, "KV cache").
- Compaction: a context ceiling can be set per session; when the history nears it, older turns are summarised. `[compaction] max_context_tokens` is the global setting.
- MCP tools can be exposed lazily (`--mcp-tools auto|eager|deferred`).

## Planning

Todo plans are phases, then lanes, then items. Ask for a plan first on big jobs.

## Sessions

- `jcode --resume` lists sessions; `jcode --resume <id>` opens one.
- Inside the app `/resume` opens a picker; Enter resumes here, Ctrl+Enter in a new terminal.
- Typing while the AI works queues a "soft interrupt" message that is injected without cancelling the run.
- One-off: `jcode run "message"` (add `--json` or `--ndjson`).

## Memory

Memories are stored locally per project and globally. Recall uses a relevance service (Jev) reached through OpenRouter and similar providers, using `OPENROUTER_API_KEY`; the doc says default builds have no local embedding model. Meaning: memory text can leave the machine. Treat memory as non-private; the owner can keep memory off for personal projects (the `[agents]` memory settings exist in config; check the doc before editing).

## Command safety

The `bash` tool has a gate that refuses or asks for a reason before destructive commands (for example `rm -rf` on a wide target); catastrophic targets such as the home folder are refused. The file says it is the main protection, so it is not a sandbox. Read what the agent proposes.

## How to give jcode a task well

1. State the goal in one sentence and the folder.
2. Name the files or behaviour that matter.
3. Say how success is checked (a test command, or what to see on screen).
4. Say limits: "do not touch other folders", "ask before installing anything".
5. Ask for a plan, approve it, then let it work.
6. Review diffs and run the test yourself.
7. Commit in small steps so each can be undone.

## Not verified

- Tool list is from file names, not from running jcode.
- Exact config keys for turning memory off.
- Windows behaviour of the bash gate (the code skips a scratch-dir step on Windows).
- Performance numbers in the README are the authors' claims.
