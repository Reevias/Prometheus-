# Typed links, graph walks, background upkeep

Source: https://github.com/garrytan/gbrain (MIT, as of 2026-11-10)

Files read: `docs/architecture/RETRIEVAL.md`, `docs/guides/{graph-ranking,temporal-edges,cron-schedule,enrichment-pipeline,dream-patterns}.md`, `src/core/cycle.ts`.

## Typed links without model calls

When a trusted local page is written, a parser scans the body for:
1. Markdown links: `[Alice](people/alice-example)`.
2. Wikilinks: `[[people/alice-example|Alice]]`.
3. Typed blockquotes such as `> **Convention:** see [path](path)`.

It then infers a link type from the sentence around it, with plain rules: `attended`, `works_at`, `invested_in`, `founded`, `advises`. Edges are saved in batches. No model is called.

Trust boundary: remote (agent over the network) page writes skip inline extraction. Their links appear later through a sweep, a maintenance run (`gbrain extract links`), or an explicit `add_link`.

An edge is unique per (from page, to page, type). `amends` links are references only and take no part in ranking.

## Dated relationships

Edges can have `valid_from` and `valid_until`. Dates come from:
- timeline lines with a cue ("Left [Acme] to join [Widget]" ends one job and starts the next);
- an explicit line: `- **2025-03-01** | me - Ended works_at [[companies/acme-example]]`;
- frontmatter `since` / `until`;
- past-tense words ("former CTO of");
- manual `add_link` with dates.
Dated evidence beats undated evidence. A nightly check looks for relationship conflicts.

## Graph walks

- `traverse_graph` and `gbrain graph` do breadth-first search. Each page is visited once at its shortest distance; each edge is returned once.
- Cap: 20,000 visited pages, shallowest first.
- Soft-deleted pages, private pages and out-of-scope sources never relay a walk.
- `rank_entities` counts distinct edges per page (inbound, outbound, both). Self-links, deleted pages and duplicates do not count. Defaults: direction `in`, limit 20.
- `get_backlinks` lists who links to a page. Relationship questions ("who works at X?") use the graph path.

## The enrichment and upkeep cycle ("dream cycle")

`gbrain dream` runs phases in a fixed order, files first, then the index:
1. `lint --fix` and `backlinks --fix` (file edits only).
2. `sync` (database picks up the file changes).
3. `synthesize` (conversation transcripts to pages).
4. `extract` (links and timeline entries) then `extract_facts` (reads the `## Facts` table).
5. `patterns` (themes seen in at least 3 reflections; `dream.patterns.min_evidence`).
6. `embed --stale` (embed only new or changed text).
7. `orphans` (report pages with no links).

Phases that write take a lock; two cycles cannot overlap. `gbrain autopilot` is a long-running daemon that runs cycles; `gbrain jobs` is a retry queue.

Suggested schedule from the docs: email and social collection every 30 minutes, meeting sync 3 times a day, weekly maintenance (`doctor`, embed stale, orphan detection), nightly dream cycle.

## Tiered enrichment (spend by importance)

| Tier | Who | API calls |
|---|---|---|
| 1 | Key people and partners | 10-15, all sources |
| 2 | Notable people | 3-5 |
| 3 | Minor mentions | 1-2 |

Always search the brain first (free), keep raw API responses for reprocessing, and cite every source.

## For Prometheus

Parse links with regexes; store typed edges in a table; BFS with a visited set; run a nightly job: re-index changed files, extract links, embed changed items, list orphans. Enrichment that calls outside services must not send private text.

## Not verified

- Full list of phases (there are more: repair, atoms, skill optimisation).
- Exact regex rules for link-type inference.
- Cost figures.
