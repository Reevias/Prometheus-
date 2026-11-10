# Understand-Anything

Source: https://github.com/Egonex-AI/Understand-Anything (MIT, as of 2026-11-07)
Files read: README.md, understand-anything-plugin/skills/understand/SKILL.md, understand-anything-plugin/agents/ (names, tour-builder.md), install.ps1 (name only).

## What it is

A plugin that analyses a project with several AI agents and builds a knowledge graph of files, functions, classes and dependencies. A web dashboard shows it. It also handles docs and "LLM wiki" knowledge bases.

## How the graph is built

1. Phase 0: pick the project folder, check Node and pnpm, build the plugin once if needed, decide full or incremental run (it records the git commit).
2. `project-scanner` agent lists files, languages and frameworks.
3. `file-analyzer` agents run in parallel (up to 5 workers, 20 to 30 files per batch) and produce nodes and edges.
4. `architecture-analyzer` assigns layers (API, Service, Data, UI, Utility).
5. `tour-builder` writes a guided tour of 5 to 15 steps in dependency order.
6. `graph-reviewer` checks the graph (inline rule check by default; full AI review with `--review`).

The README describes a hybrid: tree-sitter parses structure deterministically (imports, definitions, calls); the language model adds meaning (summaries, tags, layers, tours). Output goes to `.ua/knowledge-graph.json` (older projects keep `.understand-anything/`).

## Install

- Claude Code: `/plugin marketplace add Egonex-AI/Understand-Anything` then `/plugin install understand-anything`.
- Other agents (Codex, Gemini CLI, OpenCode, Cursor, Copilot, Devin, Kiro, and more): one-line installer. On Windows PowerShell: `iwr -useb https://raw.githubusercontent.com/Egonex-AI/Understand-Anything/main/install.ps1 | iex` (it clones to `~/.understand-anything/repo` and links the skills). Update: `& "$HOME/.understand-anything/repo/install.ps1" -Update`. Do not pipe a script to the shell without the owner's approval; offer to read it first.
- Needs Node.js 22+ and pnpm 10+ (the skill tells the user this).
- Codex uses `$understand` instead of `/understand`.

## Commands

| Command | Purpose |
|---|---|
| `/understand` | Build or update the graph. Flags: `--full`, `--auto-update`, `--no-auto-update`, `--review`, `--language <code>`, `--exclude "<globs>"`, or a folder path |
| `/understand-dashboard` | Open the interactive web dashboard |
| `/understand-chat <question>` | Ask about the codebase |
| `/understand-explain <file>` | Deep dive on a file or function |
| `/understand-onboard` | Make an onboarding guide |
| `/understand-diff` | Show what current changes affect |
| `/understand-domain` | Business domains, flows, steps |
| `/understand-knowledge <wiki folder>` | Graph of a notes/wiki folder |

`--auto-update` installs a post-commit hook that patches the graph after each commit. A `.understandignore` file and `--exclude` limit what is analysed.

## Dashboard

Nodes are colour-coded by layer; click one to see code, relations and a plain-English summary. Search works by name or meaning. A domain view shows business flows. A persona setting changes detail level. Anyone can view a committed graph without an AI: `npx https://github.com/Egonex-AI/Understand-Anything/releases/latest/download/understand-anything-viewer.tgz /path/to/project` prints a local URL with a token. Needs Node 18+.

## Costs and privacy

- The README warns the first `/understand` can use many tokens on big projects.
- It suggests a local model (for example through Ollama) for privacy. The owner's Ollama `qwen3.5:2b` is small; expect weak summaries.
- Source text is sent to whatever model the host agent uses. For private folders, do not run it on a cloud model.
- The graph folder (`.ua/`) may be committed; check it contains nothing private first.

## Not verified

- Nothing was run; flags are from the skill file.
- Windows installer behaviour (install.ps1 not read in full).
- Quality of output with small local models.
- Exact dashboard port.
