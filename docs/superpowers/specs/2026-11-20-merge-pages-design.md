# Fewer pages: merge, Research in the chat box, Study & Money (design, 20 Nov 2026)

## Why
The owner: "remove System from the sidebar … merge whatever we can into whichever so it isn't a mess". Ten pages plus
Approvals, Connections and Settings is too many places to look. Owner's answers (20 Nov): merge even more (Memory into
Settings too); Research becomes a toggle in the chat box; Study and Money become one page.
Recorded as an M2 fix from the laptop check (navigation), not a new feature: nothing is added that didn't exist.

## Goals
- The sidebar row shows only four pages, so **More disappears**: Home, Tasks, Abilities, Study & Money.
- Every old page and address still works (it opens its new home), so bookmarks, the phone and old links don't break.
- Nothing is lost: every control that exists today is somewhere in the new places.

## Non-goals / anti-goals
- No feature changes inside the pages (only where they live), except the Research toggle.
- Settings must not turn into one endless page: it gets a tab list (below).

## Approaches considered
- **Merge into fewer pages with tabs (picked: "merge even more")**.
- My first proposal (Memory stays its own page). Not picked.
- Only move System into Settings. Not picked.

## Where everything goes
| Today | New home |
|---|---|
| Home | Home (Ctrl+1) |
| Tasks | **Tasks** (Ctrl+2), tabs: Running · Waiting for you (Approvals) · Scheduled (Automations) · History |
| Approvals (profile menu) | Tasks → Waiting for you |
| Automations | Tasks → Scheduled |
| Agents, Skills | **Abilities** (Ctrl+3), tabs: Agents · Skills |
| Research → Study, Money tabs | **Study & Money** (Ctrl+4), tabs: Study · Money |
| Research → Research tab | a **Research** toggle in the chat box (below); its saved sources go to Settings → Memory → Research sources, its past searches to Tasks → History |
| Memory | Settings → Memory |
| System | Settings → System |
| Connections / Integrations | Settings → Connections |
| Chats (all) | "Show all chats" in the sidebar (the page stays) |
| Projects (all) | the PROJECTS heading in the sidebar is a link (the page stays) |

Old addresses redirect: `/v3/agents` and `/v3/skills` → `/v3/abilities?tab=…`, `/v3/automations` →
`/v3/tasks?tab=scheduled`, `/v3/approvals` → `/v3/tasks?tab=waiting`, `/v3/research` → `/v3/study`, `/v3/memory`,
`/v3/system`, `/v3/connections` → `/settings#memory|system|connections`.

**Settings** gets a tab list on the left (a row of tabs on phones): General · AI · Voice · Memory · Connections ·
System · Advanced. The existing sections are grouped under these tabs; nothing else in Settings changes.

## The Research toggle
A **Research** button in the chat box, left of the mic. Off by default. When on (orange), the message gets a deep
search with sources: it goes to the research agent (the same as typing `@research`), and the answer lists its sources
under it. It switches itself off after sending, so it never stays on by accident. Ctrl+R toggles it.

## Also in this round (small fixes the owner asked for)
- Mic button colours: **green** when voice is on (pulsing while it listens), **red** when off or paused (Stop all).
- With the side panel closed and the sidebar shrunk, the chat uses the full width (it stayed a narrow column).
- Phones: every page above checked at 360–390 px wide (no sideways scrolling, tabs scroll, touch targets ≥ 40 px).

## Left to the builder
- Exact tab styling (reuse the existing tab look), the Research toggle icon, how Settings tabs are remembered.
