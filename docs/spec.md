# Prometheus v1 — Design Spec

Status: **draft for review** · 2026-11-09 · Requirements: [`brief.md`](brief.md) · Roadmap: [`guide.md`](guide.md)

## 1. Goal

A personal AI "brain" on the user's Windows laptop. You type a task in a browser tab; a lead AI splits it
into steps, hands each step to the best agent, the agents share one memory, and you get one combined
answer. Every action is logged, can be undone, and runs under strict safety rules.

**v1 success test:** *"sort my Downloads folder and summarise any PDFs about taxes"* → plan shown → files sorted
(undoable) → tax PDFs summarised by the local model → one answer → everything visible in the activity log.

**Not in v1:** voice, gestures, phone app, remote access, image/video generation, Hermes/Argon wiring (v1.1+).

## 2. Constraints

- Windows laptop, 8 GB RAM, no GPU. Python **3.12**. Must stay light: no Electron, no Node build step.
- **No API keys yet.** Runs with the local model (Ollama `qwen3.5:2b`) or a fake model; keys plug in later.
- Personal file contents never leave the laptop.
- Built and tested in the cloud session (Linux); the user runs it on Windows. Code must be cross-platform.

## 3. Architecture change from the brief

The brief chose "build on OpenJarvis". Because the OpenJarvis install on the laptop is not confirmed and we
are building in the cloud, **v1 is a small standalone core** with clean adapters. OpenJarvis pieces
(WASM sandbox, voice, extra tools) plug in through those adapters once confirmed on the laptop.
This keeps v1 light on 8 GB, testable here, and easy for the safe self-editor to work on.

## 4. Layout

```
prometheus/
  pyproject.toml
  prometheus/
    config.py          settings file + env vars, data folder
    server.py          FastAPI app, routes, login
    models/            model providers (one interface)
      base.py  fake.py  ollama.py  anthropic.py  gemini.py
      router.py        picks a provider per purpose; enforces privacy
    memory/
      store.py         SQLite: chats, notes, memories, tasks, agent notes, audit
    safety/
      paths.py         what folders may be read / changed
      permissions.py   per-agent tool allowlist + "ask first" actions
      approvals.py     approval queue (approve / reject)
      budget.py        cost meter + cap
      killswitch.py    stop everything
      audit.py         activity log
    actions/
      journal.py       undo journal (crash-safe)
      files.py         move / rename / recycle with checks
    orchestrator/
      planner.py       plan → pick → run → combine
    agents/
      base.py  file_sorter.py  research.py  documents.py  repo_scout.py
    selfedit/
      patcher.py       the 6-check safe patch pipeline
    web/
      templates/ static/   (htmx vendored locally, no CDN)
  tests/
```

Data lives outside the code, in `%LOCALAPPDATA%\Prometheus\` (Windows) or `~/.local/share/prometheus/` (Linux).

## 5. Components

### 5.1 Model providers and router
- One interface: `complete(messages, system, purpose, json_schema=None) -> Reply` (text, token usage, cost).
- Providers: **Fake** (tests, no-key mode), **Ollama** (local, `qwen3.5:2b`), **Anthropic** (`claude-opus-5-5`,
  adaptive thinking, server-side refusal fallbacks on), **Gemini** (Argon; stub until the key arrives).
- Router maps a **purpose** to a provider from settings: `plan`, `combine`, `research`, `private`.
- **Privacy rule in code:** `purpose="private"` can only resolve to a local provider; asking for a cloud one raises an error.
  Agents that read file contents must call with `private`.
- Missing key → that provider is "unavailable"; the router falls back to the next configured provider and says so in the UI.

### 5.2 Memory (SQLite, one file)
Tables: `chats`, `messages` (structured: role, text, time — never re-parsed from text), `chat_notes`, `global_memories`,
`tasks`, `steps`, `agent_notes` (each agent writes notes the others can read), `audit_log`, `approvals`, `costs`.
Search with SQLite FTS5 (keyword) in v1; meaning-search embeddings added later.
One write path with a lock, so threads can't overwrite each other (bug seen in the friend's Jarvis).

### 5.3 Orchestrator (HuggingGPT pattern)
1. **Plan:** the planner model gets the task + short context (no file contents) and returns a JSON plan:
   steps with `agent`, `goal`, `inputs`, `depends_on`. Validated against a schema; invalid → one retry → error.
2. **Pick:** each step's agent must exist in the registry and be allowed by permissions.
3. **Run:** steps run in order of dependencies (sequentially in v1). Each result + notes go to memory.
4. **Combine:** the planner writes one answer from step results.
Kill switch is checked before every step and every model call.

### 5.4 Agents (v1)
| Agent | Does | Model |
|---|---|---|
| **File Sorter** | Scan a folder → rules first (type, date, duplicates by content hash, old installers) → for unclear files the local model suggests a folder → plan → execute through `actions/files.py` | local |
| **Research** | Web search (DuckDuckGo, no key) → fetch pages → text extraction → summary with sources | cloud if available, else local |
| **Documents** | Read PDF/DOCX/TXT locally → summarise / answer questions | local only |
| **Repo Scout** | GitHub search for a task → list with stars, last update, **license** → approval request; clone into a quarantine folder only after approval; never runs code | planner model for ranking |

Every agent declares its tools; permissions enforce the list.

### 5.5 Safety
- **Paths:** readable root = the user's home folder. Always blocked: system folders, Program Files, AppData
  (except Prometheus data), browser profiles, `.ssh`, password-manager folders, the Prometheus code folder.
  Changes (moves/renames) only inside the folder named in the task. Links are never followed or moved.
- **Never delete:** "delete" = Recycle Bin (`send2trash`).
- **Undo journal:** written after every change (write-then-rename so a crash never leaves half a file); undo a whole run
  or a single entry; refuses to overwrite; skips files changed since the scan. (Our own code, inspired by TidyByte's design.)
- **Permissions:** allowed without asking: read, move/rename/sort, web/API calls. Ask first: everything else
  (clone repos, apply self-edits, generate media, anything unknown).
- **Budget:** each cloud call records cost; warn at 80 % of the cap, stop at 100 %. Cap set in settings.
- **Prompt-injection rule:** text from files, pages and repos is wrapped as data and the system prompt says it is never instructions.
- **Web fetch guard:** no requests to localhost or private IP ranges.
- **Server:** binds to `127.0.0.1` by default; login required (password hashed with a slow hash, session cookie,
  login rate limit, CSRF token on forms). LAN access is an explicit setting.
- **Secrets:** only from environment variables / `.env`; never logged; never sent to the browser.

### 5.6 Self-editing
- **Level 1 (automatic):** Prometheus may edit its own prompt files and memory. Each change is committed to a local git
  repo inside the data folder, so it can be rolled back.
- **Level 2 (approval):** Prometheus may *propose* a new tool file. The patcher runs the 6 checks (no duplicate,
  single anchor, works on a copy, parses, tests pass in a separate process with a time limit, diff shown) and queues
  an approval. Only after "Approve" is it written, with a backup.
  **In v1, AI-written tools are not executed on the real system** — only their syntax/tests run, in a separate
  process with a time limit. Running them for real waits for proper sandboxing (OpenJarvis WASM) on the laptop.
- **Level 3 (locked):** `safety/`, `selfedit/`, `orchestrator/` and `server.py` can't be patched by Prometheus.

### 5.7 Browser UI (FastAPI + Jinja2 + htmx, served locally)
Pages: **Chat** (with per-chat notes), **Status** (live steps per agent), **Approvals** (approve / reject),
**Memory** (view, delete, wipe), **History** (runs + undo), **Settings** (providers, budget, folders, kill switch).
Updates every second or two by polling (simple, light). Image annotator: later.

## 6. Data flow (example)

1. You: "sort my Downloads and summarise tax PDFs".
2. Planner (cloud or local) → plan: `[file_sorter(Downloads), documents(tax PDFs from step 1)]`.
3. File Sorter → rules + local model → moves files → journal → notes "12 PDFs, 3 look like tax".
4. Documents → reads the 3 PDFs locally → summaries (local model).
5. Planner combines → answer. The planner sees only notes like "3 tax PDFs: …summary…", never raw files.

## 7. Errors

- Model unavailable → fallback provider, shown in the UI; no provider → clear message, task paused.
- Step fails → recorded with the reason; later steps that depend on it are skipped; the answer says what failed.
- File operation refused by the OS → skipped + reason; the journal stays valid.
- Crash mid-run → on restart, unfinished runs are marked "interrupted" and stay undoable.

## 8. Testing

- `pytest`, runs in the cloud and on Windows. Fake model for all orchestrator/agent tests.
- File tests use temporary folders with generated files (duplicates, PDFs, images with EXIF).
- Must-pass safety tests: blocked paths, no overwrite, undo restores, kill switch stops, private purpose never
  reaches a cloud provider, budget stop, patcher rejects duplicates/locked files, login required on every route.
- Planner quality (TaskBench) later, once a real model key exists.

## 9. Install and run (on the laptop)

```
git pull
cd prometheus
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
prometheus init      # sets the password and settings
prometheus serve     # opens http://127.0.0.1:8000
```

## 10. Open points (don't block the build)

- OpenJarvis on the laptop (for the sandbox and voice later).
- API keys (Claude, Gemini) — added in settings later.
- Exact model for photo tagging (SigLIP 2) is v1.1 with the photo-content tags; v1 sorts photos by facts (date, type, duplicates).
