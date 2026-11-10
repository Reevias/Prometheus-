# Hybrid search

Source: https://github.com/garrytan/gbrain (MIT, as of 2026-11-10)

Files read: `docs/architecture/RETRIEVAL.md`, `docs/guides/search-modes.md`, `src/core/search/hybrid.ts`, `src/core/search/source-boost.ts`, `src/core/ai/defaults.ts`.

## The strategies

| Arm | What it does | Needs a model or key? |
|---|---|---|
| Keyword (BM25) | Exact words, names, identifiers | No |
| Vector | Meaning, synonyms, paraphrase | Yes, an embedding model (default `voyage:voyage-4`, 1024 dimensions, needs `VOYAGE_API_KEY`) |
| RRF fusion | Merge the two rankings | No |
| Source and page boosts | Prefer curated pages | No |
| Cross-encoder reranker | Reads query and candidate together and re-orders | Yes (default `voyage:rerank-3-lite`) |
| Graph | Follow typed links | No |

Why combine: keyword alone misses paraphrases; vector alone misses exact names and relations; graph alone is blind to unlinked pages.

## How scores combine

1. Each arm returns a ranked list of pages (best chunk per page).
2. Reciprocal rank fusion: a result's score is the sum over arms of `weight / (K + rank)`, with `K = 60` (`RRF_K` in `hybrid.ts`). A page ranked high in both arms wins without needing score scales to match.
3. Intent weights (`query-intent.ts`, no model): queries are classed `entity`, `temporal`, `event`, `concept` or `general`. Entity queries lean on the graph; temporal queries skip source boosts so recent chat pages surface; concept queries lean on vectors.
4. Boosts after fusion: compiled-truth chunks get a 2.0 multiplier (`COMPILED_TRUTH_BOOST`); source-tier boost by folder prefix.
5. Optional rerank, then optional autocut (off by default; it cut recall in tests).

## Source-tier boost defaults (`DEFAULT_SOURCE_BOOSTS`)

| Prefix | Factor |
|---|---|
| `originals/` | 1.5 |
| `writing/` | 1.4 |
| `concepts/` | 1.3 |
| `people/`, `companies/`, `deals/` | 1.2 |
| `meetings/`, `media/articles/`, `media/repos/` | 1.1 |
| `yc/`, `civic/` | 1.0 |
| `daily/` | 0.8 |
| `media/x/` | 0.7 |
| `archive/` | 0.5 (demoted, not hidden) |

Override with `gbrain config set search.source_boosts "wiki/:1.3,daily/:1.0"`. Folders like `test/`, `attachments/`, `.raw/` are excluded outright.

## Mode bundles

| Knob | conservative | balanced (default) | tokenmax |
|---|---|---|---|
| Token budget | 4000 | 12000 | off |
| Default result count | 10 | 25 | 50 |
| Reranker | off | on | on |
| Relational retrieval | off | on | on |
| Query expansion | off | off | off |

Query expansion (an LLM rewrites the query) lowered recall in one test (93.2 percent down to 54.9 percent when variants had equal weight), so it is off by default.

## What works with no model and no key

- `gbrain search`: keyword arm only, instant. Works from day one on a local embedded database (PGLite) with `gbrain init --pglite --no-embedding`.
- Responses say when ranking is degraded (for example `keyword_only_embedding_disabled`).
- Intent classification, RRF, boosts, graph walks and auto-linking are all model-free.
- After the first embedding run, the vector arm and full hybrid (`gbrain query`) turn on.

## Choosing a lookup

| Need | Verb |
|---|---|
| Known name or exact token | `search` |
| Concept or "all of X" | `query` (hybrid) |
| Known slug | `get` |
| Literal phrase like grep | `search --exact` |

## Prometheus advice

Start with SQLite full-text search (BM25). Add a local embedding model (not a cloud one for private notes). Fuse with RRF at K = 60. Add folder boosts. Add a reranker last, only if it runs locally.

## Not verified

- Exact SQL behind the boosts.
- Whether the 2.0 compiled-truth boost applies to every page type.
- Benchmark numbers are from GBrain's own tests and may not transfer.
