# craft-anything

Source: https://github.com/storytold/craft-anything (MIT OR Apache-2.0, as of 2026-11-09)
Files read: README.md, docs/claude-code.md, docs/ROADMAP.md (local-model notes), docs/STATUS.md (searched).

## What it does

It studies a target (website, desktop app, phone app, game, files), has a coding agent build a new project from what it learned, compares the build with the original, and refines it. The README calls it early alpha and "100% Rust". It also offers 320 inspection tools, each as a CLI command and an MCP tool.

## Workflow

1. Study: look at the original (pages at several widths, visible text, layout, colours, fonts; for programs, files and behaviour).
2. Build: a coding agent writes a new project in the stack you name.
3. Check: compare build and original (websites at three screen widths; games via their own self-test), listing what still differs.
4. Ready: open or play the result, or ask for changes. It can run several refine rounds.

A project folder holds `craft.json` (request, status, history), `capture/`, `app/` (the generated project), `checks/`, `investigation.cany/` (evidence) and `inbox/` (open questions). Default location: `Documents/Craft Anything`.

## Clean room or Deep

| Mode | What it may look at |
|---|---|
| Clean room (default) | Only what a visitor sees: screenshots, visible text, measured layout. Refuses tools that read code or files inside the original. |
| Deep | Everything, for private use: program code and files, a site's JavaScript, Ghidra if installed. |

The README says Clean room is a rule about which tools are offered, not an operating-system sandbox. The coding agent still acts with your permissions. CLI flags: `--deep` or `--clean-room`.

## Install on Windows

1. Easiest: download the Windows installer (`.msi`) or portable zip from the GitHub Releases page (x64 for normal PCs). Releases are alpha; installers are said to be code-signed. Verify against `SHA256SUMS`.
2. From source (needs Rust 1.97+): `git clone https://github.com/storytold/craft-anything`, then `cargo run --release -p craft-anything` (app) or `cargo install --path apps/craft-anything-cli` (CLI). Compiling on 8 GB RAM will be slow.
3. Check: `craft-anything-cli doctor` reports missing coding agents and engines.

## CLI commands (from README)

- `craft-anything-cli commands` lists tools.
- `craft-anything-cli mcp` serves tools to an MCP client.
- `craft-anything-cli setup` connects it to coding-agent apps.
- `craft-anything-cli rebuild <url or -> "<request>"` with options such as `--rounds 1`, `--continue-in "<folder>"`, `--project-folder`, `--existing edit`.

## Models and agents

Building needs a coding agent already on the computer. Claude Code is supported first: craft-anything runs the owner's own `claude` program in headless mode with its own sign-in, and says it never handles the account (docs/claude-code.md). Copilot CLI and Grok are reachable through ACP; Codex is unverified. For questions and analysis, Anthropic, OpenAI, OpenRouter and local OpenAI-compatible servers can be set in Settings, AI.

Ollama: ROADMAP.md records a live test with Ollama and `qwen2.5-coder:14b` on a Windows machine with a strong GPU; the build stopped early, and later fixes got a partial result. Local small models are described as unreliable builders. A 2B model on a CPU laptop should not be expected to work.

## Approvals and safety

- Commands of a new kind trigger an approval card (allow once, always for this project, don't allow). Do not choose "always" for `npm` unless the project's dependencies are trusted: installs run package scripts. `npm install --ignore-scripts` avoids that.
- Dev servers are refused; a fixed list of build commands is allowed.
- Responsible use: only software you own or may examine; it does not bypass DRM, logins or anti-cheat; the agent is told not to copy logos, photos or text.

## Outputs

A runnable project in `app/`, check results in `checks/`, evidence records with confidence and limits.

## Not verified

- Nothing was run or installed.
- Exact Windows installer file names and current release version.
- Whether Ollama with a small model can complete any build.
- Whether the Claude Code integration is acceptable under the owner's subscription terms; the README leaves that to the user's agreement with Anthropic.
