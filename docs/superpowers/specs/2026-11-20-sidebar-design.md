# Sidebar redesign and microphone button (design, 20 Nov 2026)

## Why
The owner's laptop check: "navigation is too complex", "I need to press 4 times just to open a chat in a project".
Today the sidebar is 10 page links and no chats: a project chat takes Projects → project → its chats → the chat.
The owner wants it "like Claude's" (screenshot of the Claude Code sidebar): **the most efficient and aesthetic**.
Recorded as an M2 fix from the laptop check, not a new feature (the scope freeze stays).

## Goals
- Any recent chat in **1 click**; a chat in a project in **1–2 clicks** (2 when the project is folded).
- Chats and projects are what the sidebar shows; pages are tucked away but one click (More) or Ctrl+K away.
- Keep the v3 look (colours from tokens.css only, the mini sphere, the orange accent) and keep it calm.
- A microphone on/off button next to Stop all (owner: "yes").

## Non-goals / anti-goals
- No page is removed; no feature changes. Nothing becomes harder to reach than today.
- No new settings (CLAUDE.md: Settings is cluttered).
- Not a failure-prone rewrite of the pages themselves: only the sidebar and the top bar change.

## Approaches considered
- **A. Like the owner's screenshot (picked by the owner: "the most efficient and aesthetic")**: actions on top,
  then Projects (each folds open to its chats), then Recents, pages under More.
- B. Keep the 10 page links and add Recent chats under them: long sidebar, projects still take extra clicks.
- C. Two tabs (Chats | Pages): an extra click every time you switch.

## The design
```
 (sphere) PROMETHEUS            «
 ⊕ New chat
 ⌕ Search                  Ctrl+K
 ⌂ Home     ☰ Tasks     ⋯ More ▸        More: Agents, Chats (all), Projects (all), Skills, Memory,
                                         Automations, Research, System, Integrations
 PROJECTS                        +
  ▾ Physics project
      Light reactions notes
      Exam plan
      + New chat here
  ▸ Prometheus
 RECENTS
    Hey                          ⋯      ⋯ = Rename, Move to project, Pin, Delete
    Aeroplanes research
    Weather
    Show all chats
 ──────────────
 (P) Saiveer                     ⚙
```
- **New chat** starts a chat (as the Home command bar does). **Search** opens the existing Ctrl+K palette, which also
  searches chats.
- **Home** and **Tasks** stay visible (builder's call: the two pages used most after chats). **More** opens a small
  menu with the other pages, each with its Ctrl+number shortcut as today (Ctrl+1…Ctrl+0 keep working).
- **Projects**: every project that isn't in the trash, newest activity first; click the name to fold/unfold its
  chats (up to 8, newest first, plus "+ New chat here" and "All chats in this project"); the open/closed state is
  remembered on this device. The project's own page is one click on its name's arrow menu (⋯ → Open project).
  "+" next to PROJECTS makes a new project (the existing form).
- **Recents**: the 15 newest chats that aren't in a project (pinned first), then "Show all chats". The open chat is
  highlighted. ⋯ on hover (always visible on phones): Rename, Move to project, Pin, Delete (existing endpoints).
- The lists refresh when a chat is created, renamed, moved or deleted (no full page reload needed for the list).
- **Shrunk sidebar (Ctrl+B)**: icons only (New, Search, Home, Tasks, More); the lists hide.
- **Phones**: the same sidebar in the drawer.
- **Microphone button** in the top bar, left of Stop all: shows on (mic) / off (mic struck through) / listening
  (pulsing). Click switches voice on or off and saves it (the same as Settings → Voice). While Stop all is on it is
  greyed out and says why. Only shown when voice is set up on this computer.

## Constraints
- Python/FastAPI/Jinja2/HTMX/vanilla JS; colours only from tokens.css; works at 360 px wide; the click-through test
  must still click every control with no errors.
- Windows first: checked in Chromium here and in GitHub's Windows click-through; the laptop is the real check.

## Left to the builder
- Exact spacing, icons (existing icon set), and how many recents fit before scrolling.
- Whether the lists are part of every page render or fetched once by HTMX (`/api/v3/sidebar`), as long as they are
  current after every change.
