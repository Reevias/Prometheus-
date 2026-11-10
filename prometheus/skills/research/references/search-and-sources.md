# Search tools and source filtering

Source: https://github.com/assafelovic/gpt-researcher (Apache-2.0, as of 2026-11-09); https://github.com/dzhng/deep-research (MIT, as of 2026-04-11)

Read from `.claude/references/retrievers.md`, `gpt_researcher/actions/retriever.py`, `actions/web_scraping.py`, `scraper/`.

## Search tools (retrievers)

| Name (`RETRIEVER` value) | Key needed | Notes |
|---|---|---|
| duckduckgo | none | Free, default choice for a no-key setup |
| arxiv | none | Papers |
| semantic_scholar | none | Papers |
| pubmed_central | none | Biomedical papers |
| searx | `SEARX_URL` | Needs a SearX server (you can self-host) |
| tavily | `TAVILY_API_KEY` | GPT Researcher default; free tier exists (not checked) |
| google | `GOOGLE_API_KEY` + `GOOGLE_CX_KEY` | |
| bing | `BING_API_KEY` | |
| brave | `BRAVE_API_KEY` | |
| serper / serpapi / searchapi | one key each | Paid search wrappers |
| exa | `EXA_API_KEY` | |
| openalex | `OPENALEX_API_KEY`, `OPENALEX_EMAIL` | Papers |
| bocha, crw, groundroute, xquik, getxapi | one key each | Niche |
| mcp | per server | Tools from MCP servers |

deep-research (dzhng) uses only Firecrawl (`FIRECRAWL_KEY`, or `FIRECRAWL_BASE_URL` for a self-hosted one) for both search and page text.

Rule of thumb: start with a no-key tool (duckduckgo for the web, arxiv/semantic_scholar/pubmed for science). Add a keyed tool only if results are thin.

## Using several tools

`RETRIEVER` takes a comma list, for example `tavily,arxiv`. Every sub-query is sent to every tool. Unknown names fall back to the default.

## Custom retriever contract

A retriever is a class with `__init__(query, headers)` and `search(max_results)` returning a list of `{title, href, body}`. That is all a new search tool needs to provide.

## Scrapers (page text)

`SCRAPER` default `bs` (BeautifulSoup, free). Alternatives: browser (JavaScript pages), firecrawl and tavily_extract (hosted, keys), pymupdf (PDFs; chosen when a link is a PDF), arxiv. `MAX_SCRAPER_WORKERS` 15 and `SCRAPER_RATE_LIMIT_DELAY` (seconds between requests, default 0) control politeness.

## How sources are filtered

1. **Domain exclusion:** `filter_urls()` removes any URL containing an excluded domain string. Empty or non-text URLs are dropped.
2. **Already visited:** `visited_urls` is shared across sub-queries and deep research branches, so a page is read once.
3. **Domain restriction:** `query_domains` can limit searches to chosen sites.
4. **Relevance filter:** chunks are scored by similarity to the sub-query (threshold 0.42) and the top 10 kept.
5. **Curation (optional):** `CURATE_SOURCES=True` asks the model to rank up to 10 sources by relevance, credibility, recency, objectivity and presence of numbers. It is told to keep rather than drop when unsure.
6. **Deep research:** learnings and URLs are de-duplicated; context is capped at 25,000 words.

## Practical advice for Prometheus

- Read the page text yourself before trusting a search snippet.
- Prefer primary sources (official docs, papers, filings) over aggregators.
- When two sources disagree, say so in the report.
- Search queries leave your machine: never put personal data in them.

## Not verified

- Free-tier limits of Tavily and other paid tools.
- Whether DuckDuckGo scraping stays reliable; it has no official API.
- `jev` context filter details.
