# Prometheus 3.0: Master Plan & Architecture

One source of truth for the Prometheus 3.0 implementation. Merges product vision, agent harness architecture, Cloudroom integration, repository intelligence, and security.

## 1. Product Vision
Prometheus is a personal AI operating system with a reliable agent harness at its core.
- **Assistant**: Natural language and voice.
- **Agent Harness**: Dynamic execution, tools, task graphs, and recovery.
- **Skills**: Progressive-disclosure capability packages invoked by slash commands.
- **Projects & Memory**: Durable context, provenance, and cross-session continuity.
- **Device Control**: Permissioned desktop, browser, and Android.
- **Execution Backends**: Local first, optional Cloudroom.
- **Trust**: Least privilege, approvals, audit, undo, and global STOP ALL.
- **Open-Source Integrity**: All original source code across M0–M10 remains open source under the MIT License, preserving local autonomy without mandatory proprietary services. See [AGENTS.md](../../AGENTS.md).

## 2. Design & Visual Identity
- **Palette**: Near-black canvas `#0B0B10`, surfaces `#17171F`, raised `#211D1A`, primary orange `#FF922B`, amber `#FFC06A`, text `#F4F1ED`, muted `#AAA5A0`.
- **Orbital Identity**: Glowing orange orbital core with subtle particle animation.
- **Default Home**: Minimalist. Sidebar, central orbital mark, Prometheus label, and one command input.
- **Contextual Slash Palette**: Palette appears ONLY after typing `/`. No repo panels or static examples cluttering Home.
- **Real Telemetry Only**: Status cards appear only when backed by live system stats.

## 3. Agent Execution Engine
- **Two Paths**: Fast path for simple deterministic tasks; Full ReAct orchestration for complex tasks.
- **Four Foundational Primitives**:
  1. `edit_file_exact`: Anchored exact string matching, atomic write, diff verification, backup/journal.
  2. `terminal_exec`: Persistent streaming PTY, cancellation, process-tree cleanup.
  3. `read_file_window`: Windowed line ranges, ripgrep/ast-grep, Tree-Sitter symbol search.
  4. `browser_action`: Playwright/CDP, accessibility tree, snapshots/diffs.

## 4. Repository-to-Skill Intelligence
- Inspect repository safely in read-only mode.
- Tree-Sitter symbol mapping and stack profiling.
- Match existing skills or draft new least-privileged skills with `SKILL.md` and manifests.
- Expose stable slash commands.

## 5. Cloudroom Integration
- Provider-neutral `ExecutionRuntime` protocol with `LocalRuntime` default.
- Optional `CloudroomRuntime` for isolated cloud VM sandboxes and live thread teleportation.
- Session receipts, request IDs, ordered event streaming, and failure recovery.
