# Prometheus — Working Brief

*(Project renamed from "Prometheus" to **Prometheus** on 2026-11-09. Older notes may still say Prometheus.)*

Status: **requirements complete, design defaults approved** (user: "everything as you say", 2026-11-09). Next: written spec. This file collects
every decision made so far so nothing is lost. The design spec comes after the
section-by-section design review.

Last updated: 2026-11-10

---

## 1. What it is

A personal "brain" made of several AIs that share one memory. A lead AI reads
each request, splits it into subtasks, and hands each subtask to the agent or
tool that does it best. It runs on a PC, is controlled from an Android phone,
by voice, and by hand gestures.

## 2. Hardware and platform

| Item | Value |
|---|---|
| Brain host | Windows PC/laptop, stays on most of the time |
| RAM | 8 GB |
| GPU | None (CPU only) |
| Phone | Android |
| Language | English |

Budget: the user provides a Claude API key; everything else should be open
source / free where possible (the user finds and supplies alternatives).

Consequences:
- Local models must be small (~2B). Heavy thinking goes to Claude (API) or Ollama Cloud.
- Image/video generation cannot run locally; later it uses free cloud GPUs.
- Everything must stay light: no Electron, no PyQt WebEngine, effects off by default.

## 3. Build order

The full vision is four projects. Each gets its own spec, plan and build.

1. **Engine (first):** shared memory + lead AI (orchestrator) + 3 agents, on the PC.
2. Tool/agent connectors (more agents, media generation).
3. Phone app (Android: files, photos, notifications, voice).
4. Extras (more gestures, smart features).

**Base framework:** OpenJarvis (Apache-2.0) with Claude as the engine, our own
code as plugins in one clearly separated folder. Fallback if it is too heavy on
8 GB: our own small Python engine.

## 4. Version 1 scope (the engine)

- Lead AI uses the HuggingGPT 4-stage pattern: **plan → pick agent → run → combine answer**.
- First agents: **file & photo sorter**, **web research**, **document helper**.
- Sorting layout is chosen per request (date, content, trip, …); the user can always specify.
- Photo sorting strategy for a CPU-only PC: **facts first** (date, location, type,
  duplicates — instant, no AI), then **content tags slowly in the background /
  overnight** with a small local model.
- Agents share memory: each agent writes notes/results that the others and the lead AI can read.
- Planner quality is tested with **TaskBench** (from Microsoft JARVIS).
- Success test: type *"sort my Downloads folder and summarise any PDFs about taxes"*,
  the lead AI splits it, agents share notes through memory, one combined answer comes back,
  every action is logged and can be undone.

## 5. Permissions

| Action | Rule |
|---|---|
| Read files & photos | Allowed without asking |
| Move / rename / sort files | Allowed without asking, **never deletes** (recycle bin + undo log) |
| Web search / call APIs | Allowed without asking |
| Generate media (uses GPU/cloud credits) | Ask every time |
| Anything else | Ask every time |

Privacy: personal files and photos **stay local**. Only short text summaries go to Claude.
Coding and web research may use cloud models (Claude / Ollama Cloud) because they
do not involve personal files; anything personal goes to the local model.

## 6. Self-editing

| Level | What it can change | Safeguard |
|---|---|---|
| 1 — automatic | Prompts, notes, memory, how it splits work | Every change in git, rollback anytime |
| 2 — sandboxed | Writes **new tools/plugins** | Runs in sandbox, tests must pass, **user approves** before live |
| 3 — locked | Permission system, safety rules, kill switch, orchestrator, the patcher itself | Can only *propose*; user reviews |

Required checks before any self-edit is saved (learned from the friend's Prometheus,
where the AI inserted the same block twice):

1. Skip if the change is already present (no duplicates).
2. Insertion point must match exactly once, otherwise refuse.
3. Edit a copy, never the live file; the copy must parse (`ast.parse`).
4. Run tests on the copy (minimum: it loads; better: real tests) inside the sandbox.
5. Show the diff and get the user's approval.
6. Backup / git commit first; auto-restore if the app fails to start afterwards.

Lesson from Brahma's "Crucible": blocking a list of Python imports and running the
code as a normal process with a timeout is **not** a real sandbox (blocklists are
easy to bypass, and the code keeps the user's full permissions). Use real
isolation (OpenJarvis WASM sandbox) for AI-written code.

References to study: jcode "self dev mode", OpenJarvis learning loop (DSPy) and
WASM sandbox, Brahma "Crucible"/"Auto-Heal" (ideas only — see licenses).

## 7. Must-have safety features

- Per-agent permissions; risky actions ask first.
- Never delete, only move to a recycle bin; every file action undoable.
- Activity log + live status.
- Cost meter with a monthly spending cap.
- Kill switch (button, voice word, and open-palm gesture).
- Model routing: cheap/local models for simple jobs, Claude for planning.
- Memory rules: user decides what is remembered and what never leaves the device.
- Treat content the agents read (files, web pages) as data, never as instructions.
- Tests for every agent and tool.
- Secrets only in environment variables / gitignored `.env`; never in chat or git.

## 8. Ideas taken from the friend's Prometheus (`gui.py`, `main.py`)

Keep (ideas, rewritten in our own code unless the friend agrees to reuse):
- Two memory levels: **per-chat notes** and **global memories**, with a screen to view, delete or wipe them.
- **Approve before saving** files the AI creates (choose and rename).
- **Image annotator**: draw on a screenshot before sending it.
- **Model switcher** and max-tokens control.
- **Stop-speech** button.
- Tkinter is very light — candidate for a later floating mini-bar.

Avoid (bugs found):
- Self-edit inserted duplicate code → covered by the checks in section 6.
- Permanent delete (`os.remove`) → recycle bin + undo.
- Annotator adds a new image layer on every mouse move → memory grows/lag.
- History file read/written from several threads; reload overwrites unsaved state.
- Chats stored as plain text and re-parsed by `You:` prefixes → store structured messages.
- Hard-coded `G:\jarvis_ai` path → configurable data folder.
- Commands (e.g. model switching) sent as chat text → separate control actions.
- `main.py` passes the `(reply, files)` pair straight to `speak()`.

Still missing: `assistant.py` (the friend's brain and self-edit code) and the friend's permission to reuse code.

## 9. Interface, voice, gestures, reporting

- **UI:** a lightweight browser tab (extend the OpenJarvis frontend).
- **Gestures (MediaPipe Hands, only when switched on, low resolution/FPS):**
  thumbs up = approve, open palm = stop all agents, swipe = next, a gesture to wake/mute. No mouse control.
- **Voice (chosen by Claude, user delegated):** speech-to-text **faster-whisper `base.en`** on CPU;
  text-to-speech **Kokoro** (already in OpenJarvis, fast on CPU), **Chatterbox Nano** as an option to compare;
  voice and gestures ship in **v1.1, right after v1** (v1 is tested by typing).
- **Wake word:** **yes** ("Hey Prometheus": openWakeWord has no ready model for this name — train a custom one (its training notebook makes synthetic samples), or use the pre-trained "Hey Jarvis" model meanwhile; to verify), plus gesture wake and push-to-talk.
- **Reporting:** notifications, spoken replies, live status screen, daily summary.
  In v1 (no phone app yet) the engine keeps the activity log and status records that the phone app will use later.

## 10. Tool roles (each used only for its intended job)

| Job | Tool | Model |
|---|---|---|
| Coding | **Claude Code** (official app) on the user's **subscription**; or **jcode** with an **API key** (Claude, Gemini/Argon or Ollama) | Subscription for Claude Code; API key for jcode |
| Understanding software | Craft Anything, REA (only on software we may analyse) | Claude or Ollama Cloud |
| Teaching | Understand-Anything (inside jcode / Claude Code) | — |
| Teaching / explaining code | **Graphify** (Apache-2.0/MIT): maps a project into a knowledge graph you can ask questions about ("explain X", "how does A reach B"). Code is parsed locally with no AI; docs/PDFs/images use an AI model, so only point it at code or non-private docs. Use now inside Claude Code to explain Prometheus's code; later an **Explainer** agent in Prometheus (v1.1). | Local for code; Claude/Gemini for docs |
| Research | GPT Researcher (+ dzhng deep-research only with Firecrawl) | Claude / Ollama Cloud; local Ollama for embeddings |
| Finding open-source apps | sindresorhus/awesome | — |
| Small private jobs | Local Ollama (~2B model) | stays on the PC |

More tools may be added later; these are the preferred ones.

Tool needs: GPT Researcher needs a search provider (free: DuckDuckGo or SearXNG);
dzhng deep-research needs a Firecrawl key (paid) or self-hosted Firecrawl;
REA runs inside a coding agent and is only used on software we may analyse.

Additional brains and helpers (user, 2026-11-09):

| Name | Role |
|---|---|
| **Claude** | **Main brain**: lead AI / planner ("my brain is mainly you") |
| **Hermes Agent** | Not set up yet; will run on the user's **desktop** (a second machine). Later: connect it over Tailscale and/or import its skills into OpenJarvis. |
| **Gemini 4 Argon** (Google) | Second brain: deep reasoning, second opinions, backup when Claude's credit runs out, image understanding of non-private images. Needs a Gemini API key (later). Same privacy rule as Claude: no personal files. |
| **Repo Scout** (new agent) | Searches GitHub for repos that fit a task, shows a short list with license and safety notes, and **waits for the user's approval** before cloning or using anything. Found code always runs in the sandbox first. |

Connectors (Gmail, Drive, calendar, …): the user connects them later.
Long-term memory: user believes it is handled (OpenJarvis memory). We still define the shared-memory layout on top of it (tasks, agent notes, chat notes, global memories) and verify it on the user's PC.

## 10b. Setup checklist (user's PC)

Commands from each tool's docs; not yet run on the user's machine.

- [x] Install **Ollama** for Windows (ollama.com). Local use needs **no API key**.
- [ ] `ollama signin` (only needed for Ollama Cloud models).
- [x] `ollama pull qwen3.5:2b` — small local model for private jobs (~2.7 GB, done).
- Note: Ollama **cloud models returned 402 (need credits)** on the user's free plan → coding uses real Claude.
- [ ] Optional: pull a model tagged **cloud** for heavy jobs.
- [ ] Install **jcode**: `irm https://jcode.sh/install.ps1 | iex` (PowerShell); run with `--provider claude`.
- [x] Get a **Claude API key**; store it with `setx ANTHROPIC_API_KEY "<key>"` (never paste it in chat or commit it).
- [x] **Revoke the Ollama API key that was posted in chat** and create a new one; store as `OLLAMA_API_KEY`.
- [ ] Finish the OpenJarvis install and report: does it start, does the Claude engine work, RAM use.

## 11. Component status

Already covered by OpenJarvis / chosen tools: local model (Ollama), embeddings,
vector memory (FAISS + BM25), web search (DuckDuckGo/Tavily), webpage reading,
PDF reading, speech-to-text (faster-whisper), text-to-speech (Kokoro),
browser UI, code sandbox (WASM), approval store.

Partly covered: phone link (OpenJarvis Telegram channel works as a quick interim
phone connection), image generation (OpenJarvis only ships paid DALL·E — still
need open source), undo (approvals exist, undo does not).

Handled by us (no search needed): duplicate photos (imagehash), photo info/EXIF
(Pillow), undo for file moves (Send2Trash + move log).

User is searching (v1 priority first):
1. Photo tagging model (small CLIP-style, CPU)
2. OCR for screenshots/scans
3. Wake word
4. Hand tracking (confirm MediaPipe on Windows)
5. Phone link: notifications + secure remote connection

Later: Android file manager reference, open-source image and video generation on free cloud GPUs.

### Extra tools reviewed (2026-11-09)

| Tool | Verdict | Role |
|---|---|---|
| HunyuanVideo-1.5 | ✅ fits, later | Video model. Needs a GPU (14 GB, ~6 GB via Wan2GP) → run on free Colab/Kaggle GPU, user approves each job. Tencent Community License: not valid in EU/UK/South Korea. |
| Wan2GP (found via the above) | ✅ fits, later | Low-VRAM runner for Hunyuan / Wan / LTX video models on the cloud GPU. License not yet checked. |
| Chatterbox (Resemble AI, MIT) | ✅ fits | Spoken replies. **Nano (110M) runs on CPU** ~3× realtime on 8 cores; Turbo (350M) for better quality. Alternative to Kokoro. Voice cloning only with consent. |
| free-llm-api-resources | 🟡 fits as a list | Free text-model APIs (Groq etc.) for cheap model routing. Not video. The linked copy is stale (last commit 2024-08) — use the maintained original. |
| open-generative-ai (MIT) | 🟡 reference only | Nice media studio UI, but generation runs through the **paid MuAPI** service; local generation needs an NVIDIA/AMD GPU. Not our free video engine. |
| pgvector | ⏸ not for v1 | Postgres vector search. Needs a Postgres server (extra RAM, setup on Windows). OpenJarvis FAISS + BM25 covers v1; reconsider if memory must be shared across machines. |
| TidyByte (Krinal24/folders-organizer, PR #7 merged) | ✅ design fits, ⚠️ **no license** | Almost exactly our file-sorter agent: rules first (duplicates by hash, installers), AI suggests in batches (gemma3:4b via Ollama), user reviews and edits the plan, plain code applies it. PR #7 adds the undo: crash-safe journal saved after every change, never overwrites, deletes go to the Recycle Bin, skips files changed since the scan, stays inside the folder, 19 tests. Gaps: renames-in-place not supported, undo is per whole run, deleted files must be restored from the Recycle Bin by hand. No LICENSE file → ideas only unless the author allows reuse. Author is not a friend of the user → **we write our own version of the design**; reuse code only if the author adds a license or gives written permission. |
| EasyOCR (Apache-2.0) | ✅ fits | OCR. CPU mode (`gpu=False`) works; needs CPU PyTorch (shared with Chatterbox/SigLIP). Last commit 2025-12. Lighter fallback: RapidOCR. |
| EmbeddingGemma | 🟡 different job | Text-only embedding model → **cannot tag photos**. Good candidate for memory search instead of nomic-embed. |
| SigLIP 2 (Google) | ✅ suggested | Image-text model like CLIP, small versions run on CPU → photo tagging. Alternative: Gemma 3 4B (describes images, slower) for overnight tagging. Not yet verified on the user's PC. |
| Dyad (Apache-2.0 core) | ⏸ optional | AI app builder (like Lovable/Bolt), Electron-based (heavy on 8 GB). Overlaps with jcode; maybe for quick UI prototypes. |

## 12. Licenses

- OpenJarvis, Microsoft JARVIS, jcode, Craft Anything, REA, Understand-Anything,
  GPT Researcher, deep-research: MIT / Apache-2.0 — fine to use.
- Brahma AI Evo: personal-use, no-distribution license — **ideas only, no code**.
- Friend's Prometheus: ideas only until the friend agrees to code reuse.

## 12b. Notes for the phone-app project (later)

- Android allows whole-storage access ("All files access"), but Google Play restricts
  which apps may use it — likely sideload the app, or check Play policy first (not yet verified).
- Phone speech-to-text: Android's built-in on-device recognition; replies via Android text-to-speech.
- Remote access to the PC from anywhere: e.g. Tailscale; notifications: e.g. ntfy (user is evaluating).

## 13b. Subscription vs API key (Anthropic policy, Feb 2026)

Anthropic's Claude Code usage policy: *"OAuth authentication (used with Free, Pro, and Max plans) is intended exclusively for Claude Code and Claude.ai. Using OAuth tokens obtained through Claude Free, Pro, or Max accounts in any other product, tool, or service — including the Agent SDK — is not permitted."* Breaking it can get the account restricted.

| Tool | Subscription allowed? | What to use |
|---|---|---|
| Claude Code (official) | ✅ | Log in with the Claude account; answer **No** if it offers to use `ANTHROPIC_API_KEY` (or start it with the variable cleared) |
| claude.ai | ✅ | — |
| jcode | ❌ (third-party tool) | An API key: Claude, Gemini (Argon) or Ollama |
| Prometheus | ❌ (separate product) | An API key (Claude / Gemini) or the local model |

Not done: having Prometheus run the `claude` CLI in the background to borrow the subscription. Policy limits assume "ordinary, individual usage"; an always-on assistant is borderline and risks the account.

API keys come **later** (user decision, 2026-11-09). Until then Prometheus runs with the **local model** (Ollama) or a **fake model** for tests; every brain is a plug-in provider, so adding a key later needs no code change.

## 14. Security — "nobody can hack my PC"

No system is unhackable; these rules make Prometheus a hard target and limit damage if something goes wrong.

**Network**
- The Prometheus server listens on **localhost only** by default. Phone/LAN access is opt-in.
- Remote access only through **Tailscale** (private, encrypted). **Never port-forward** on the router.
- **Login required** on every page and API call (strong password, session tokens, rate-limited login attempts).
- Ollama stays on `127.0.0.1` (never set `OLLAMA_HOST=0.0.0.0`).
- Windows Firewall and Defender stay on.

**Secrets**
- Keys only in environment variables or a gitignored `.env`; never in chat, code, logs or git.
- Secret scanning before every push.
- **Spending limit:** Claude may spend only the credit loaded on the API key — **prepaid credits with auto-reload OFF** in the Anthropic console, so it cannot spend more. Prometheus also tracks spend, warns at 80% and stops at the remaining balance.
- 2-factor login on GitHub, Anthropic, Ollama, Google and Tailscale accounts.

**What agents can touch**
- **Folder access** (user: "everything on my laptop"; Claude's safe version): agents may read the whole **user folder** (`C:\Users\<you>`); moves happen only in the folder named in the task.
  Always blocked, even with full access: Windows/system folders, Program Files, browser profiles, `.ssh`, password managers, the Prometheus code itself (except via the self-edit pipeline).
- Each agent has a **tool allowlist**; no agent gets a raw shell. Code runs only in the sandbox.
- Web fetches cannot reach `localhost` or private network addresses (SSRF protection, OpenJarvis `ssrf.py`).
- Text from files, web pages and repos is **data, never instructions** (prompt-injection rule + OpenJarvis injection scanner).
- Never delete — Recycle Bin + undo journal.

**Code from outside**
- Repos found by Repo Scout: user approval → clone into a quarantine folder → run only in the sandbox → never run their install scripts on the real system.
- Python packages installed only from a reviewed list, with pinned versions.
- Self-edits follow the 6 checks in section 6; the safety core is locked.

**Account and recovery**
- Run Prometheus under a **standard (non-admin) Windows account**.
- Turn on **File History or OneDrive backup** before the first real sort.
- Audit log of every action; kill switch (button, voice word, open palm).
- Keep Windows, Python, Ollama and OpenJarvis updated.

## 15. If the laptop is not enough

Later option: move the brain to a **cheap cloud server (VPS)** reachable through Tailscale.
Trade-offs: monthly cost; personal files would leave the laptop, so the file/photo agent would stay on the laptop
(or the phone) and only the planner and research agents would move. Providers and prices: to research when needed (NOT_CHECKED).

## 16. Decisions made by Claude (user: "everything as you say")

- Prometheus lives in **this repo** in a separate `prometheus/` folder (code) and `docs/prometheus/` (docs); can move to its own repo later.
- Python **3.12 or newer, no upper limit** (owner asked for the latest). Tested here on 3.12 and 3.14; GitHub Actions tests 3.12, 3.13 and 3.14 on Windows and Linux.
- Voice: faster-whisper `base.en` + Kokoro + openWakeWord "Hey Prometheus"; in v1.1.
- Photo tagging: SigLIP 2 (small); OCR: EasyOCR (RapidOCR fallback); memory search embeddings: EmbeddingGemma or nomic-embed (pick by test).
- Build it here in the cloud session, test with fake files, push to GitHub; the user pulls and runs on Windows.
- Phone in a browser at home (password) → later Tailscale for anywhere → later the real Android app.

## 17b. Gaps between today and the final vision (updated 2026-11-10)

| Area | Missing |
|---|---|
| **Quality** | No test set of everyday tasks yet (owner to send 2 or 3 bad examples); the Claude key is not added yet, so answers still run on random OpenRouter models; the Haiku request shape and a real Antigravity or GitHub call are unconfirmed |
| **Planning** | The planner makes one plan and runs it: no loop that looks at a result and adapts (for example open an app, check it opened, then continue); steps run one at a time |
| **Seeing the screen** | No screenshot understanding or computer-use loop, so clicking works only by exact coordinates |
| **Laptop: files** | Moving, copying, finding and recycling files anywhere (and on other drives) is not built; the File Sorter works inside one folder |
| **Laptop: browser** | No browser automation (forms, logins, downloads, reading the page you have open) |
| **Laptop: testing** | None of the Windows commands has run on a real laptop; Wi-Fi status parsing assumes English Windows; no WPA3-only networks |
| **Memory** | No links between memories (graph), no replace-old-fact handling (Mem0/Zep style), no scheduled tidy-up (gbrain "dream cycle"). Built: running chat summaries; chunked documents with word + meaning search (v1.4; meaning search needs `ollama pull nomic-embed-text`) |
| **Output** | No PDF, Word, Excel or slide generation (the owner wants PDFs with links for roadmaps); no image or video generation (needs a cloud GPU) |
| **Photos** | Photo content tagging (SigLIP) and OCR (EasyOCR) from the v1.1 plan are not built |
| **Interaction** | No voice (wake word "Hey Prometheus", speech-to-text, spoken replies), no hand gestures |
| **Phone** | No phone access (browser on Wi-Fi, Tailscale, Android app), no notifications |
| **Autonomy** | No scheduler or proactive tasks (reminders, daily summary, watching something); background jobs do not survive a restart |
| **Connectors** | Gmail, Calendar, Drive, Notion not connected (owner: later); Hermes agent not set up; Gemini Argon works only as an ordinary free provider |
| **Self-editing** | AI-written tools can be proposed and approved but are not run (needs a real sandbox) |
| **Packaging** | No start with Windows, no tray icon; no cost dashboard per model. Built (v1.4): `scripts/install-windows.ps1`, desktop and Start-menu shortcut, `prometheus update`, old-copy warning in `prometheus doctor` |
| **Security testing** | No automatic prompt-injection test run (garak, from the owner's stars, could probe the local model); offensive tools in the stars (evilginx2, sliver, cameradar, maigret, Deep-Live-Cam) are deliberately not part of Prometheus |

## 17c. Reference projects for holding context (researched 2026-11-10)

Mem0 (Apache-2.0): extracts facts from conversations and merges them, updating or replacing instead of duplicating. Letta/MemGPT (Apache-2.0): a small block of memory always kept in context, older material in an archive the agent searches. Zep/Graphiti (Apache-2.0): facts carry the time they were true and are marked replaced, not deleted; runs locally with a graph database. LangMem (MIT): memory tools plus background consolidation. Hindsight (MIT). Decision: borrow the methods in our own small SQLite code (8 GB RAM, no extra servers). Built: running summary of old chat turns. Still to build: replace-old-fact handling, memory links, scheduled consolidation, meaning-based search with a local embedding model.

## 18. What is built (kept up to date)

Repository: **Reevias/Prometheus-** (moved out of `Claude-testing-` on 2026-11-10; the website stays in the old repo,
which must not be wiped as a whole).

| Version | What it added |
|---|---|
| **v1** | Standalone Python core (not built on OpenJarvis): lead AI (plan → pick → run → combine), shared SQLite memory, File Sorter, Documents, Research and Repo Scout agents, path policy, undo journal + Recycle Bin, permissions, approvals, budget cap, kill switch, approval-gated self-editing, password-protected browser UI, `prometheus init / serve / doctor` |
| **v1.1** | OpenRouter as the main brain; API keys saved from Settings (password needed, never shown again); automatic model switching (5-minute rest on failure, drop to free/local at the cap, each answer shows its model); Opener agent (YouTube play + caption summary, GitHub, websites, files); desktop window (`prometheus app`); red "ember" theme |
| **v1.2** | Skill folders (`SKILL.md` + `references/`) written from the reference repos, with a Guide agent and a Skills page; catalog of free providers with a searchable settings list (three need no key); memory recall (word forms, partial words, ranking, sources) and Memory page search |
| **v1.3 (built, not yet security-reviewed or merged)** | Polished chat (formatted answers, bubbles with times and a Copy button, a "working on…" line that names the current step, no flicker, chats named from the first message, example prompts, Enter to send); **System agent** (Wi-Fi status/list/connect/disconnect, Bluetooth on/off/status, location, battery) with a fixed list of actions; **password prompt on the Approvals page** for things like a Wi-Fi password (goes straight to Windows, never stored, logged or shown to a model); **Antigravity agent** (Google's cloud agent through the Gemini API, every job needs approval); complete GitHub repo (MIT licence, tests on Windows and Linux in GitHub Actions, contributing, security and changelog files) |

| **v1.4** | **Wikipedia agent** (search + plain-text articles through the Wikipedia API, any language code, only the matching parts sent to the model, saved as chunks; also the fallback when web search fails; the planner sends encyclopedic facts it is unsure of to Wikipedia and recent things to web search). **Memory chunking**: long web pages and files are split into overlapping small chunks (about 250 tokens, 15% overlap) inside bigger parent chunks (about 1000 tokens), cut at headings, paragraphs and sentences first; search runs on the small chunks and the model reads the parent around each match; word search (BM25) and meaning search (local Ollama embeddings) are merged by rank (RRF); private file chunks never reach a cloud model; research and documents agents send only the best passages; the planner sees saved web passages; Memory page lists and deletes saved pages and files. **Always the new app**: install script, shortcuts, `prometheus update` (pull + install packages), version in the window title, old-copy warning. **From the owner's GitHub stars**: a learning-roadmap skill (OSSU, developer-roadmap, free-programming-books, system-design-primer, build-your-own-x, LLMs-from-scratch and others) and a secret check (idea from trufflehog) that blocks keys in GitHub posts and self-edits |

Reviews: every version gets a fresh security review before it reaches `main`; findings are fixed test-first.

Status on 2026-11-10:
- `main` holds v1 to v1.2 plus the repository files. GitHub Actions ran the full suite on **Windows and Linux: both passed** (commit ab30a46).
- `main` holds v1.3 (422 tests pass here on Python 3.12 and 3.14): chat polish, the smarter brain, the graphite interface, laptop control, GitHub connection, Antigravity, and the `website/` folder. The v1.3 security review is done and its findings are fixed.
- v1.2 security review: 1 critical + 4 important findings, all fixed (see section 19).
- **Not tested on real hardware**: every laptop command (Wi-Fi, Bluetooth, location, battery, apps, windows, volume, brightness, typing, clicking, power; tested only with a stand-in for Windows), a real Antigravity call, a real GitHub call, the desktop window, real OpenRouter/free-provider/YouTube calls, and the Claude Haiku request shape (thinking and effort settings are unconfirmed against the live API; if Haiku rejects them the router falls back to the next model).

## 19. Decisions since the first build

**Models and keys**
- OpenRouter is the main brain until a Claude API key exists. A Claude *subscription* may only be used inside the
  official Claude Code app; jcode's `--provider claude` signs in with the subscription, so it is not used.
- If any step of a task read personal files, the final answer is written by a local model only (or shown as a plain list if no local model is running); it is never sent to a cloud model.
- Free providers are opt-in ("Use it" plus an "I understand" tick); saving a key alone does not switch one on. Several may log or train on prompts; they carry a warning label. None can
  ever be a route for personal files. Ollama cloud models (`:cloud`) and a remote Ollama do not count as local.
- Keys live in `<data>/.env`, a key saved in the app wins over `setx`, changing a key needs the password, and
  5 wrong passwords log the user out.

**Opening things**
- Only plain YouTube video/search pages and GitHub repository pages open without asking. Other sites and local
  files need approval. Only documents and media can be opened; programs, scripts, installers, web pages, disk
  images and macro-enabled Office files never are.

**Skills**
- One folder per kind of task; the lead AI reads only names and one-line descriptions, then the guide, then a
  reference file when needed. Built-in: research, task-planning, memory, models, coding, understand-software,
  explain-code, file-sorting, video. User skills go in `<data>/skills/`. A skill is instructions the AI follows,
  so skills from other people need the user's approval. Self-editing cannot touch skills.
- Sources and reuse: gpt-researcher, deep-research, jcode, Craft Anything, REA, Understand-Anything, Graphify,
  gbrain, freellmapi, OpenJarvis, Microsoft JARVIS (permissive licences: methods described in our own words);
  awesome-free-llm-apis (CC0: data bundled); TidyByte (no licence: design only); Brahma (ideas only).

**Memory**
- One shared `memory.db`. Method follows gbrain: each memory records its source; search mixes exact words, word
  forms and partial words, ranks better and newer matches first. Meaning-based search with a local embedding
  model is a later option.

**Laptop control (v1.3, user decision 2026-11-10)**
- "Complete automation": listed system actions (switch Wi-Fi, Bluetooth on/off, read location and status) run
  straight away and are logged. It is a fixed list of actions, never a free command line.
- A Wi-Fi password is typed into a box in the app and goes straight to Windows: never to an AI model, memory or logs.
- Still blocked whatever else is allowed: Windows system folders, saved passwords and browser data, SSH keys,
  Prometheus's own safety code.

**Brain and answers (owner decisions 2026-11-10, after the owner found it "extremely dumb")**
- Cause found in the code: no chat history reached the planner, every message had to become an agent plan ("closest agent" rule), a second model call rewrote results, and the answer prompt forced filler. `openrouter/auto` also let OpenRouter pick any model.
- Fixes (built): one call answers plain questions directly (`answer` field); the last 12 turns go to the planner; facts the owner asks to remember are saved (max 3 per message, with a source); planner, answer and `style.md` prompts rewritten; unmodified old prompt copies in the data folder are upgraded automatically; answers built from personal files are stored as `private` and replaced by a placeholder in later cloud calls.
- Models: **Claude (Anthropic API, prepaid, auto-reload off) is the main brain** and writes plans and final answers; its cheaper helpers do sub-tasks: Claude Haiku 5.5, free providers, OpenRouter cheap models, and the local Ollama model (the only one allowed to read personal files). Default routes: plan/combine = anthropic, openrouter, ollama; research = anthropic:claude-haiku-5-5, openrouter, ollama; private = ollama. Prices per model are tracked (Opus 5.5 $4/$20, Sonnet 5.5 $2/$10, Haiku 5.5 $0.10/$0.50 per million tokens); Haiku gets no server-side fallback setting.
- Writing style (the owner's words): depends on the question. A simple fact gets one line, no history. A big request (for example a computer science roadmap) gets structure, real links and explanation, and the owner wants PDFs for those. **PDF output is not built yet.**
- Laptop control, owner's rule: do everything right away; **still ask first** for sending or posting anything, spending money, and installing software / power / security settings.
- Interface: neutral graphite with red only for the status signal, the main button and the current item; answers are plain text; no glows, gradients or clipped corners (method: unslop by @heydamir, CC BY 4.0).

**Old repository (owner decision 2026-11-10)**
- The sign-up/login website was copied into `website/` here (all files byte-identical). The old repo `Reevias/Claude-testing-` is **left alone**: besides the website and the old Prometheus branch it holds five unrelated projects (a love-heart animation, a portable toolkit installer, flashcard/quiz screens, a watch tool, a trading strategy). The owner deletes that repo on GitHub when ready.

**Laptop control (built 2026-11-10, owner rule: act right away; ask first for sending/posting, spending, installing/power/security)**
- Fixed actions in `prometheus/laptop.py`, run by the System agent: open/close apps, list open windows, minimize/maximize/focus, volume, mute, brightness, dark mode, screenshot (saved to Pictures\Prometheus), type text, press keys from a fixed list, click, lock/sleep/shutdown/restart/logoff, install with winget, plus Wi-Fi, Bluetooth, location, battery.
- Everything the owner asks for (app name, text, numbers) travels in an environment variable read by a fixed script, never inside command text. Programs are called by their full System32 path on Windows.
- Never opened or typed into: terminals and admin tools (PowerShell, cmd, Windows Terminal, WSL, regedit, mmc, Task Manager). Typing, key presses and clicks **fail closed**: if Windows can't say which window is in front, nothing is sent.
- Asks first (approval): pressing Enter or Ctrl+Enter, any click (Prometheus can't tell what is under the pointer), sleep/shutdown/restart/logoff, installing software, joining an open Wi-Fi network (open profiles are never auto-joined). Does right away: everything else in the list, including lock.
- Gentle close only (the app can ask to save); Prometheus never closes itself.
- Not built yet: moving or copying files anywhere on the laptop (File Sorter still works inside one folder), browser automation, PDF output.
- Known limits: Wi-Fi status parsing uses English `netsh` labels, so it may fail on other Windows languages; WPA3-only networks are not supported; brightness works on the built-in screen only.

**GitHub connection (built 2026-11-10)**
- Settings has a GitHub repository field (default Reevias/Prometheus-) and the GitHub key. Make a fine-grained token limited to that one repository: Contents, Issues and Pull requests read/write; Actions and Metadata read. The token is only sent to api.github.com.
- The github agent reads freely (latest commits, whether tests passed, open issues and pull requests, file contents). Everything that posts or changes waits for approval: create an issue, comment, propose a one-file change (new branch + pull request, never main, never `.github/`), update itself (`git pull --ff-only` in its own checkout, then restart).

**v1.3 security review (2026-11-10): 0 critical, 11 important, all fixed test-first**
- Old private answers would have reached the cloud planner after the upgrade: the migration now marks every existing assistant message private.
- Antigravity ignored the spending cap: it now checks the cap before starting, counts an estimated cost ($3 per million tokens), stops and cancels on the kill switch or an error.
- Approvals could run twice on a double click: now claimed atomically. A mistyped Wi-Fi password no longer uses up the approval.
- Saved memories: facts are only saved when the owner's own message asks ("remember", "don't forget", "note that", ...), and anything that looks like a secret is never saved.
- Wi-Fi names and passwords must match in full (no trailing line breaks, control characters, or over-long names); the password file is overwritten before deletion, retried if Windows holds it, and leftovers are swept at startup.
- Output of Windows tools is read in the Windows console encoding.

**What went wrong in the owner's real test (screenshots, 2026-11-10) and the fixes**
- "open youtube": the planner picked the opener but left its target empty, so the answer said no link was given; "just the app" repeated it; "open youtube browser" asked for a URL. Fixes: a step missing a required input is sent back to the planner with the reason (one retry); the opener works out the target from the request for well-known sites; the YouTube and GitHub home pages open without asking; the planner prompt now says app = system agent, website = opener, with the YouTube example.
- "use deepseek's model" ran the repository search. Fix: a `settings` agent shows the models and switches one after approval (applied at once and saved).
- The screenshots show the older red interface and the old filler sentence about undo, so that laptop was still on an older version.

**Python (owner decision 2026-11-10)**: 3.12 or newer, no upper limit; tested on 3.12 and 3.14 here, and GitHub Actions tests 3.12, 3.13 and 3.14 on Windows and Linux.

**Final answers and privacy (found by the v1.2 review)**
- A security review found that the step that writes the final answer sent document summaries to a cloud model. Fixed: if any step read personal files, the answer is written by a local model only, or shown as a plain list when no local model is running.
- Every free provider is labelled "may log or train on prompts"; turning one on needs an "I understand" tick; saving a key does not switch one on. Free providers stay usable at the spending cap (paid use on the owner's account is not tracked).
- Skill files that are links are ignored; skill files saved by Windows tools with a hidden marker (BOM) are read.

**Chat polish (v1.3)**
- Assistant answers are escaped first, then bold, `code`, lists and headings are added; never links, images or raw HTML. Your own messages are shown as typed.
- The message list only redraws when something changed.

**Laptop control (how it is built)**
- `prometheus/laptop.py` runs one fixed command per action: `netsh wlan ...` for Wi-Fi and fixed PowerShell scripts for Bluetooth, location and battery. Commands are lists, never a shell string. Wi-Fi names are checked (no quotes, line breaks or special characters).
- Connecting to a saved or open network happens straight away and is logged. A secured network that is not saved asks for the password on the Approvals page. The password is written to a short-lived profile file that is always deleted, and it is never kept in the database, audit log or any prompt.
- Network names and location are treated as personal: answers about them are written by a local model.
- Needs Windows. Location needs Windows location services switched on.

**Antigravity (v1.3, user decision 2026-11-10)**
- Direction chosen: Prometheus sends jobs to Google's Antigravity agent through the Gemini API (needs a Gemini API
  key; it is a cloud service, so no personal files). The other direction (Antigravity calling Prometheus as an MCP
  tool server) is a later option.
- What Google's docs say (read 2026-11-10, preview): agent id `antigravity-preview-09-2026`, called through the
  Interactions API; it works in its own Linux sandbox on Google's computers (runs code, browses the web, builds
  files) and cannot see this laptop. Default model Gemini 3.8 Flash; Gemini 4 Argon can be chosen. A token limit
  per job (`max_total_tokens`) stops runaway cost. Typical cost $0.25 to $1.30 per job, up to about $5 for long
  ones; free tier projects get a free quota. Limits: no structured output, text and image input only.
- Prometheus: every job waits for approval (it costs money), the job text is shown in full, the token limit is 200,000
  per job, the result is posted back into the chat. Needs a Gemini key in Settings and `pip install -e ".[antigravity]"`.
- Antigravity's own login must not be reused in other tools; Prometheus uses a normal Gemini API key only.

**Repository**
- Licence: MIT. Tests run on GitHub for Windows and Linux on every push.

**Not tested yet (as of v1.2)**: anything on real Windows, the desktop window, real OpenRouter / free-provider /
YouTube-caption calls. The owner's first run on the laptop is the real test.

## 17. Open items

- [ ] **Laptop control, still to build**: files and folders anywhere on the laptop (find, open, copy, move, rename, recycle; extra allowed folders and drives in Settings), browser and web tasks. Done: apps, windows, settings and hardware, typing and clicking.
- [ ] Make a GitHub token (fine-grained, one repository) and paste it in Settings so the github agent works.
- [ ] PDF output for big answers (roadmaps, study plans) with links.
- [ ] Settings: choose the planner model and the helper models separately; show which model did what.
- [ ] Ask the owner for 2 or 3 exact things that went wrong, to build a small test set of "basic tasks".
- [ ] **First real run on the laptop** from the new repo (`git clone https://github.com/Reevias/Prometheus-.git`), then report what breaks. Test Wi-Fi, Bluetooth and location commands there first.
- [ ] Buy Anthropic API credit (prepaid, auto-reload off) and add the key in Settings: this is what makes answers good. Later a Gemini key (Argon, Antigravity).
- [ ] Backup (File History / OneDrive) switched on before the first real sort.
- [ ] Old repo `Claude-testing-`: left alone by the owner's choice; they delete it on GitHub when ready.
- [ ] Polish still open: Settings page layout, the status panel, approvals page.
- [ ] Antigravity the other way round: Prometheus as a tool server (MCP) that Antigravity can call.
- [ ] v1.x voice ("Hey Prometheus" needs a custom wake-word model), gestures (confirm MediaPipe Hands on Windows), phone in the browser, Tailscale.
- [ ] Later: OpenJarvis sandbox for running AI-written tools; Hermes on the desktop; image/video generation on a cloud GPU; Android app.
- [ ] Smaller review leftovers: one bad model name rests the whole provider for 5 minutes; Remove cannot delete a key set with `setx`; the browser may offer to save an API key as a password; OneDrive-moved Documents folders are not found by name.
- [ ] Optional: ask TidyByte's author (Krinal24) to add a licence; friend's `assistant.py` + permission to reuse.
