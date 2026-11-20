# Fewer Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans (owner chose native). Steps use checkbox syntax.

**Goal:** Four sidebar pages (Home, Tasks, Abilities, Study & Money), Settings in tabs that each show only their own
settings, a Research toggle in the chat box, mic colours, a full-width chat, phones checked.

**Architecture:** "Page groups": pages that merge share one tab bar (`v3/tabs.html`, a macro taking the group and the
active tab) and keep their own addresses, so every existing link, form `data-then`, test and phone bookmark keeps
working; the sidebar marks the group's page as active. Settings' own tabs (General, AI, Voice, Connections, Advanced)
are views inside `/settings` switched by the address hash; Memory and System are separate pages in the same tab bar.

**Tech Stack:** FastAPI, Jinja2, HTMX, vanilla JS, tokens.css, pytest, Playwright Chromium.

**Spec:** `docs/superpowers/specs/2026-11-20-merge-pages-design.md`

## Global Constraints
- Colours only from tokens.css. No browser prompt(). No setting added or removed. Old addresses keep working.
- Ctrl+1 Home, Ctrl+2 Tasks, Ctrl+3 Abilities, Ctrl+4 Study & Money; Ctrl+, Settings. Every other page stays in the
  Ctrl+K palette.
- Works at 360–390 px: no sideways scroll; tab rows scroll sideways inside themselves.

## Review Focus
- A link to an old section anchor (`/settings#money-tracker`, `/settings#safety-check` from the phone page) opens the
  tab that holds it, scrolled to it (Task 2 test).
- Settings search finds a setting in another tab (Task 2 test).
- A POST in Settings re-renders the page: the same tab stays open (Task 2 browser test).
- The Research toggle never stays on after sending (Task 3 browser test).
- Phone width on every merged page (Task 4).

### Task 1: Page groups and the four-page sidebar
**Files:** create `prometheus/web/templates/v3/tabs.html`; modify sidebar.html, tasks.html, approvals.html,
automations.html, agents.html, skills.html, research.html, memory.html, system.html, routes.py/pages_v3.py
(redirects `/v3/abilities` → `/v3/agents`, `/v3/study` → `/v3/research?mode=study`, `/v3/research` default mode
study), sidebar.js/app.js (no More; palette keeps every page via hidden links); tests in `tests/test_v3_merge.py`.
**Groups:** tasks = Tasks `/v3/tasks` · Waiting for you `/v3/approvals` · Scheduled `/v3/automations`;
abilities = Agents `/v3/agents` · Skills `/v3/skills`; study = Study `?mode=study` · Money `?mode=money`;
settings = General · AI · Voice · Connections (in /settings) · Memory `/v3/memory` · System `/v3/system` · Advanced.
`/v3/research?mode=research` (saved research sources) shows the settings group with Memory active.
- [ ] Tests first: sidebar has exactly 4 page links with Ctrl 1–4; each group page shows its tab bar with the right tab
  marked; the sidebar marks the group (Waiting for you → Tasks); redirects; the palette still lists Memory, System,
  Skills, Automations, Research sources; the Research tab is gone from Study & Money.
- [ ] Implement, run, commit `feat: four pages in the sidebar; merged pages share a tab bar`.

### Task 2: Settings in tabs, cards laid out as tables
**Files:** settings.html, settings_sections.html (only `data-tab` on each section), settings_script.html, theme.css,
routes.py (Connections cards in Settings; `/v3/connections` → `/settings#connections`); tests in test_v3_merge.py and
the click-through.
**Section → tab:** general: features, appearance, general, notifications, focus-mode, daily-summary, safety-check,
about, log-out · ai: ai, costs, practice-tests · voice: voice · connections: (connection cards), connections,
use-prometheus-from-other-ai-apps, obsidian, github, other-keys · advanced: assignments, study-coach,
clipboard-and-screenshot-memory, money-tracker, search-inside-files, watchers, always-allowed,
places-prometheus-may-use.
- [ ] Tests first: each section has its data-tab; `#voice` shows only voice sections (browser); an old anchor opens its
  tab; search finds across tabs; after a POST the same tab is open; every setting id still exists once.
- [ ] Cards: each section a card with a title bar; each setting row a two-column grid (name + control | explanation)
  with a hairline between rows; stacked on phones.
- [ ] Commit `feat(settings): one tab at a time, cards laid out as tables`.

### Task 3: Research toggle, mic colours, full-width chat
- [ ] Research toggle `#research-toggle` in command_bar.html (left of the mic, `aria-pressed`); on send while on, the
  text goes as `@research <text>` and the toggle switches off; Ctrl+R toggles. Tests: browser sends with the prefix
  once, toggle off after.
- [ ] Mic: on = `--ok` (green; pulses while listening), off / Stop all / failed = `--bad` (red). Test: computed colours.
- [ ] Chat column: with the right panel closed it uses the width (max ~1280 px instead of the fixed narrow column).
  Test: message column width grows when the panel is closed (browser).
- [ ] Commit `feat: Research toggle in the chat box; mic colours; chat uses the width`.

### Task 4: Phones, docs, full check
- [ ] Phone screenshots (390 px) of Home, a chat, Tasks (3 tabs), Abilities (2), Study & Money (2), Settings tabs,
  Memory, System: look at each; fix overflow; extend `test_phone_width_fits` with the merged pages.
- [ ] CHANGELOG, bugs.md, PLAN.md; full suite; push; GitHub Windows run; merge into main; report.
