# Prometheus 3.0 — M2 build plan (desktop GUI)

Status: approved direction from the owner (13 Nov 2026). Sources: the owner's screenshot (design authority), the two
requirement records (Categories A–F and G–S, every category answered), and the owner's instructions:
build on `prometheus-v3`, merge to `main` once it works, then delete the old GUI. Where answers clashed the owner
asked Claude to decide; those decisions are listed in section 9.

Rules for every step: **Windows first** (native Windows CI must pass; the owner's laptop is the final check),
**no dead buttons** (a control whose feature isn't built is hidden, never shown broken or "coming soon"),
**real data only** (telemetry, status and connections are never faked; unreadable stats are hidden),
**open source only** (every new dependency and font licence checked), **stack unchanged** (Python, FastAPI, Jinja2,
HTMX, JavaScript, HTML/CSS, Canvas, PyWebView/WebView2), **performance first** (8 GB laptop, no GPU), then security.

## Step 0 — Groundwork (before any GUI work)
1. Bring `main` into `prometheus-v3`, so v3 has all of 2.3 (voice clap/ElevenLabs, translate, repo of the day,
   auto-retry, mic fixes) and the finished M0/M1. Resolve conflicts; all tests green on Linux and native Windows.
2. Review the existing M2 code (ChatGPT/Antigravity): keep what matches this plan, list what is redone. Known
   problems to fix: fake idle task ("System Monitoring", 100%), hard-coded "online", wrong sidebar links (Projects →
   history, Automations/Research → study, System → costs), permanent `/` icon, sphere too small, labels lost.
3. Fix `docs/implementation/ROADMAP.md` to the owner's M0–M10 roadmap (it still says "M2 Skills").
4. An automated **click-through test** that presses every visible button on every page and fails on any error or
   dead control (extends the existing 2.x click-through test). Runs on Windows CI from now on.

## M2.1 — The look, Home, chat and the window
**Design system** — one CSS-variables file with the palette (#0B0B10 base, charcoal panels, #FF922B orange,
#FFC06A amber, #F4F1ED text, #AAA5A0 muted; green/amber/red for status only) and a test that fails on off-palette
colours. Modern open-source sans-serif for the UI, monospace for code/logs/telemetry. Large rounded corners, thin
glowing orange borders. Dark only (light theme later). About 90% faithful to the screenshot; every intended
difference goes on a list the owner approves.

**Shell** — identical on every page: frameless themed title bar (minimize, maximize, close), sidebar, workspace,
right panel. Fluid between 1366×768 and 3840×2160 plus ultrawide; sidebar and right panel collapse progressively
on narrow windows; multi-monitor with different resolutions.

**Sidebar** — expanded with icons and labels by default, collapsible to icons (labels never lost). 14 items: Home,
Agents, Skills, Tasks, Projects, Memory, Automations, Research, Integrations, System, Chats, Files, Activity,
Settings. Active item: orange left bar, orange-tinted fill, orange text and icon. Profile at the bottom (the
owner's name from Settings, asked on first run), gear for Settings, bell with unread badge, last 20 notifications in
a dropdown, toasts. Ctrl+1…Ctrl+0 jump to the first ten; arrow keys move through items; visible focus outlines.

**Home** (before the first message) — large orbital sphere slightly above centre, greeting below, command bar,
quick actions, recent chats, suggested prompts, real system status summary.

**Command bar** — `[+] input [model chip] [mic] [send]`.
- `+` menu: Add files, Add images, Add documents, Add code files, Select project files (shown once Projects exist).
  No screenshot or screen capture.
- Enter sends, Shift+Enter new line; grows to 10 lines, then scrolls. Send is dim when empty, glows when ready,
  turns into Stop while generating, and can queue the next message.
- Attachment-only messages allowed (Prometheus asks what to do with the files); empty messages blocked.
- Typing `/` at the start, or Ctrl+/, opens the **command palette**: grouped by category, name + short description,
  includes skills, arrow keys + Enter, Esc closes. `@Agent` picks an agent for that message.
- **Ctrl+K** opens the **global palette**: search pages, chats, settings and actions.
- Ctrl+L focuses the bar, Esc closes an open menu first and otherwise stops the reply, Up arrow edits the last
  message, Ctrl+N new chat.
- Model chip shows the active model; clicking it switches model (from the models already in Settings).

**Attachments** — drag and drop anywhere with an overlay; image thumbnails, file chips (name, size, type), PDF
first-page and text previews, remove (×), upload progress, clear all. Types: images (PNG, JPG, WebP), PDF, text and
Markdown, Word, spreadsheets and CSV, code files, ZIP (contents listed; text files inside read; nothing run),
audio (transcribed with the existing speech engines). Limits in Settings, defaults **25 MB per file, 10 files per
message**. Stored in the chat's project if it has one, otherwise temporary, kept **7 days**.

**Chat** — many chats (Chats section): search, rename, delete, pin, archive; export Markdown, JSON, PDF. (Streaming
text moved to M3.) Markdown, code with syntax highlighting and copy, tables, charts, LaTeX math, inline images, Mermaid
diagrams. Message actions: copy, edit and resend, regenerate, retry on failure, stop, delete, thumbs up/down.
Copy message / code / as Markdown, download code block, download generated files, click images to enlarge, save
image. **Visible progress only**: status line, tool cards (name, inputs, result), step list, elapsed time,
collapsible details. Never hidden reasoning.

**Quick actions** — fully customisable; start set: Code, Research, Automate, More, plus Summarise a file, Write,
Analyse data, Explain, Plan, Search my files. Each pre-fills the command bar with a template. "More" opens a menu of
all actions plus "Customise". Control PC stays hidden (M6).

**Right panel** (most pages, collapsible) — real CPU, RAM, GPU, CPU temperature, disk and network usage (any stat
Windows can't read is hidden); active agent, current task and progress (or a truthful idle state); Prometheus
online/offline from real health; live audio waveform.

**Orbital sphere** — glowing core with orbital rings and a dense particle cloud; large on Home, small in the top bar
and right panel elsewhere. States: idle (breathing glow), thinking (faster orbit, brighter pulse), tool running
(distinct motion/colour shift), done/error (brief flash, then settle), listening/speaking (reacts to the real mic and
voice). Medium base speed; glow adjustable (default strong). Rich on strong PCs, automatically lighter on the 8 GB
laptop (particle count and frame rate adapt; Low/Medium/High in Settings); pauses when unfocused or minimised;
follows Windows "reduce motion" with a still glowing sphere, plus a manual switch.

**Voice** (wired to the existing 2.x voice) — continuous listening and a custom wake phrase, both off by default,
clear mic-on indicator, always-visible mic mute; auto-send after silence; spoken replies only in voice mode, stoppable
by click or key; mic permission prompt and denied message; input/output device choice; voice, speed and volume;
on-laptop or cloud speech chosen in Settings.

**Window** — opens fullscreen; F11 toggles, Esc leaves fullscreen; title-bar controls appear when the mouse reaches
the top edge in fullscreen. Close hides to the tray (reminders and voice keep working); "Quit" in the tray menu
exits. Tray quick menu, native notifications, start with Windows (option), remembers monitor and position.
Global shortcut to show Prometheus.

**Settings** — category list on the left, panel on the right: General, Appearance (accent, glow, sphere quality,
font size, density, reduce motion), Notifications, Audio, Models (switch model, details), Providers (API keys,
masked after saving), Agents, Tools, Skills, Integrations, Privacy (clear all data, pause memory and activity
logging, export data, what may go to cloud AIs), Security (permission levels for files/PC/tools, confirm risky
actions, **locked mode** = the existing Stop all, activity log), About (version, licence, updates, logs folder,
diagnostics, credits). Existing 2.x settings move into these categories; nothing is lost.

## M2.2 — The pages, on what Prometheus already does
- **Agents** — card grid + detail side panel: name, description, capability tags, status (Idle, Running, Waiting for
  approval, Error, Disabled, Offline), model, tools, last run, permissions; enable/disable. Automatic choice, `@`
  override. A simple "running now" list (the full multi-agent view is M5).
- **Skills** — card grid with list toggle, auto-grouped by type; details (description, dependencies, permissions,
  version/author, usage history); enable/disable, install/remove (from repos, as today), basic test-run.
- **Tasks** — list and table views; pending, running, completed, failed, cancelled, paused; detail with timeline,
  progress, agent/tool history, logs, outputs; retry, stop, cancel, delete; search and filters (text, status,
  project, date).
- **Memory** — search, inspect, edit, delete items; export; privacy: pause collection, clear all, exclude chats.
- **Automations** — today's reminders, recurring jobs and watchers as cards (name, next run, last result, history);
  pause/resume, edit, delete, run now; approval setting per automation; failures as a toast + Windows notification.
- **Research** — chat-style with a sources sidebar, or split view (question, sources, report); web search with
  numbered citations, file-based research, multi-step with a visible plan; source cards (title, domain, snippet),
  pin/exclude/add own, open in browser; export Markdown, PDF, Word, HTML; searchable, deletable history.
- **Integrations** — cards only for real connectors (Gmail, Google Calendar, email, GitHub, Obsidian, phone, MCP,
  …): connected / disconnected / needs re-login; connect, disconnect, permissions, test, error with a fix
  suggestion; confirm before sending or writing; every action logged.
- **System** — live telemetry detail, health of each service, costs, logs.
- **Chats**, **Files** (search inside files, generated outputs), **Activity** (history with undo, audit log).

## M2.3 — New basics (don't exist yet)
- **Projects** — cards (name, last activity, files, task progress, agents); workspace tabs: files, chats, tasks,
  agents, memory notes, settings; create blank or from a folder; import/export; delete to a restorable trash;
  research reports saved into projects; project files in the `+` menu.
- **Task runner** — pause/resume for running tasks; Kanban and timeline views.
- **Automation builder** — one-time, recurring and conditional (on today's watcher triggers), with approvals.

## M2.4 — Switch-over
New GUI becomes the only GUI; old templates, routes and static files are deleted once every feature they offered is
reachable in the new one (checked against a feature list); tests updated; native Windows CI and a run on the owner's
laptop; merge `prometheus-v3` into `main`; release.

## Later milestones (hidden in M2, never shown as dead buttons)
| Milestone | Moved there |
|---|---|
| M3 model routing | default model per agent/task type; smarter automatic agent choice; **answers appearing word by word (streaming)**, moved from M2.1 by the owner (13 Nov) |
| M4 repo-to-skill | sandboxed skill test-runs, dependency checks, skills created from repos; **the owner's repos** (large, linked to other repos): read safely, map how they connect, turn useful parts into skills and searchable knowledge (builds on M1's file tools for finding files and self-editing) |
| M5 many agents | creating your own agents, the full multi-agent view, conversation branching |
| M6 runtime / Cloudroom | Control PC; Cloudroom as an optional place to run tasks (already planned) **and to store Prometheus and its knowledge for free** (repo knowledge, backups, sync). Encrypted on the laptop before upload; the laptop copy stays the main one; works offline if Cloudroom is down |
| M7 security hardening | full per-project permissions, keys in Windows Credential Manager |
| M8 memory engine | project and conversation memory, long-term knowledge base, fully automatic memory, memory import; finding and pulling information from the repo knowledge (M4) and Cloudroom storage (M6) |
| M9 release | automatic performance tuning audit, accessibility audit, diagnostics polish, final visual sign-off |
| M10 autonomy | automations that act without asking, voice that acts on its own |

**Scope freeze (owner, 13 Nov 2026):** nothing new is added to the plan until M10 is done. New ideas are written
down in `docs/ideas-after-m10.md` and wait. The owner's repos and Cloudroom storage were the last additions.

## Acceptance for each part
Native Windows CI green (Linux too); the click-through test finds no dead or broken control; every visible number
is real; side-by-side with the screenshot, with only approved differences; idle CPU stays low on the 8 GB laptop
with the sphere running; reduced motion works; keyboard shortcuts work; the owner tries it on the laptop.

## 9. Decisions taken for the owner (clashes)
Build on `prometheus-v3` (owner) after bringing main in (step 0) · keep the good parts of the existing M2 code ·
deliver in parts M2.1–M2.4 · features needing a later engine move to that milestone and stay hidden · 90% fidelity
checked side by side against an approved list of differences · `@` mention overrides automatic agent choice ·
Esc closes menus first, then stops the reply · Ctrl+K = search everything, `/` and Ctrl+/ = chat commands ·
"More" = all actions + Customise · all 14 sidebar items, Ctrl+1…0 for the first ten · Close hides to the tray,
"Quit" exits · rich animation that calms down automatically on the laptop · owner's own name in the profile ·
the unspecified extra screen size is dropped · chat export kept (Markdown, JSON, PDF) · skills: basic working
actions (G3) · memory: automatic saving stays as in 2.x (instant "remember…"), fully automatic memory waits for M8 ·
automations: built on the existing reminders/watchers, the builder in M2.3 · ZIP and audio attachments handled
safely as above · defaults 25 MB / 10 files / 7 days · model chip switches model.

## 10. Reference note (13 Nov 2026)
The Friday Mark 2 research ([`docs/reports/FRIDAY_MARK2_RESEARCH.md`](../reports/FRIDAY_MARK2_RESEARCH.md)) rates M2
relevance as medium: only its idea of truthful agent/task status data. It changes nothing in M2 and adds no deliverable.
