# graphify

Source: https://github.com/Graphify-Labs/graphify (Apache-2.0; a LICENSE-MIT file also exists, as of 2026-11-08)
Files read: README.md, docs/how-it-works.md, LICENSE, NOTICE.

## What it is

A Python CLI plus an assistant skill. `/graphify .` maps a folder (code, docs, PDFs, images, video) into a graph you can query. The README says it uses no embeddings or vector store; it is a real graph. The PyPI package name is `graphifyy` (two y). Other similarly named packages are not official.

## Install (Windows)

1. Needs Python 3.10+ and uv. README: `winget install astral-sh.uv`.
2. `uv tool install graphifyy` (or `pipx install graphifyy`). If `graphify` is not found: `uv tool update-shell`.
3. `graphify install` detects your assistant and registers the skill. `graphify install --platform <name>` targets one (codex, gemini, cursor, copilot, and others). `--project` installs into the current repo only.
4. Optional extras: `graphifyy[pdf]`, `[office]`, `[video]`, `[mcp]`, `[watch]`, `[ollama]`, `[openai]`, `[anthropic]`.
5. Uninstall: `graphify uninstall` (`--purge` deletes `graphify-out/`).

## The flow

1. In your assistant: `/graphify .` (Codex: `$graphify .`).
2. Three passes (docs/how-it-works.md):
   - Pass 1 code: tree-sitter AST, local, no model, about 40 languages.
   - Pass 2 video/audio: local transcription with faster-whisper.
   - Pass 3 docs, PDFs, images, transcripts: a model reads them (the assistant's model, or `graphify extract --backend ...` with Gemini, Kimi, Claude, OpenAI, DeepSeek, Azure, Bedrock or Ollama).
3. Communities are found with the Leiden algorithm.
4. Output goes to `graphify-out/`.

| File | Contents |
|---|---|
| `graph.html` | Click, filter and search in a browser |
| `GRAPH_REPORT.md` | God nodes (most connected), communities, surprising links, suggested questions |
| `graph.json` | The full graph for later queries |

## Commands

- `/graphify . --update` re-reads only changed files; `--cluster-only`, `--no-viz`, `--wiki` also exist.
- `graphify query "what connects auth to the database?"` returns a small subgraph (default budget 2000, depth 2; `--budget`, `--depth`).
- `graphify path "A" "B"` shortest path between two things.
- `graphify explain "Name"` one concept with its connections. Ambiguous names are refused with candidates listed.
- `/graphify add <url>` adds a paper or video.
- `graphify hook install` rebuilds on commit; `graphify update .` after `git pull`.
- `.graphifyignore` and `--exclude` limit files; `.graphifyrc` holds settings such as `query_budget`.

## EXTRACTED versus INFERRED

| Tag | Meaning |
|---|---|
| EXTRACTED | Directly in the source (a call, an import); confidence 1.0 |
| INFERRED | Worked out by resolution or the model; score 0.55 to 0.95 |
| AMBIGUOUS | Uncertain; listed in the report for review |

When teaching, say which kind of link you are showing.

## Privacy

- Code: parsed locally; a code-only folder needs no API key and `graphify extract` runs offline. On mixed folders add `--code-only` to skip docs and images.
- Docs, PDFs, images: sent to a model. For personal files, do not do this on a cloud model.
- README says no telemetry. But every query is logged locally to `~/.cache/graphify-queries.log` (question, node count, time).
- `graphify extract` auto-picks a provider from whichever API key is in the environment; check which before running.
- The graph and report include file names and summaries; do not share a graph of private files.

## Limits

First run can cost tokens on non-code inputs; tiny projects see little saving. The README says code is "free" because no model is used.

## Not verified

- Nothing was run. Windows PATH and quoting issues are mentioned by the README but not read in detail.
- Behaviour of `--backend ollama` with a 2B model.
- Cloud product (Graphify Cloud) not covered; it is a paid service.
