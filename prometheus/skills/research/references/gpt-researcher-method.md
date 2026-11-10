# GPT Researcher method

Source: https://github.com/assafelovic/gpt-researcher (Apache-2.0, as of 2026-11-09)

GPT Researcher is a planner, executor and publisher pipeline. The main class is `GPTResearcher` in `gpt_researcher/agent.py`. Research logic lives in `gpt_researcher/skills/researcher.py` (`ResearchConductor`), writing in `skills/writer.py` (`ReportGenerator`), and every prompt in `gpt_researcher/prompts.py` (`PromptFamily`).

## Stages

1. **Choose a role.** `choose_agent()` asks the model which expert persona suits the query (for example "Research Analyst") and returns an agent name plus a role prompt. That role becomes the system message for later calls.
2. **Initial search.** `_get_initial_search_results()` runs one search on the raw query. The results give the planner real, current context.
3. **Plan.** `plan_research()` calls `plan_research_outline()`, which uses `generate_search_queries_prompt`. It asks for `MAX_ITERATIONS` (default 3) search queries as a plain list of strings. The prompt forbids search operators (`site:`, `filetype:` and so on) because many backends return nothing for them. For detailed reports the parent query is prefixed to each sub-query.
4. **Retrieve.** For each sub-query `_process_sub_query()` runs every configured retriever (default Tavily, set by `RETRIEVER`). Each gives `{title, href, body}`. `MAX_SEARCH_RESULTS_PER_QUERY` defaults to 5.
5. **Scrape.** `_scrape_data_by_urls()` filters URLs (`filter_urls` drops anything matching `excluded_domains`), skips URLs already in `visited_urls`, and fetches pages in parallel (`MAX_SCRAPER_WORKERS` 15). Scraper choice is `SCRAPER` (default `bs`, BeautifulSoup). Others in `gpt_researcher/scraper/`: browser, firecrawl, tavily_extract, pymupdf (PDFs), arxiv, web_base_loader.
6. **Compress.** `ContextManager.get_similar_content_by_query()` calls `select_context()` with `max_results=10`. Pages are chunked, embedded, and only chunks with similarity above `SIMILARITY_THRESHOLD` (default 0.42) are kept. `CONTEXT_FILTER` (auto, keyword, embeddings, none) picks the filter. With no embedding key, a keyword filter is the fallback.
7. **Curate (optional).** If `CURATE_SOURCES` is true, `SourceCurator.curate_sources()` asks the model (temperature 0.2) to rank up to 10 sources. Its prompt says: favour relevance, credibility, recency, and sources with numbers; keep unusual viewpoints; do not rewrite content; exclude only the clearly irrelevant.
8. **Aggregate.** All sub-query contexts are joined into one context string.
9. **Write.** `generate_report()` builds the prompt for the report type and calls the "smart" model (`SMART_LLM`, temperature `TEMPERATURE` 0.4).

## Report types (`ReportType` enum)

| Value | What it makes |
|---|---|
| `research_report` | Standard report, `TOTAL_WORDS` default 1200 |
| `detailed_report` | Splits into up to `MAX_SUBTOPICS` (3) subtopics, researches each, then merges |
| `outline_report` | Only a structured outline |
| `resource_report` | A bibliography-style report judging each source and how it helps |
| `custom_report` | Your own prompt plus the context |
| `deep` | Recursive deep research (see deep-research-loop.md) |

Other settings: `REPORT_SOURCE` is web, local, or hybrid; `REPORT_FORMAT` default APA; `LANGUAGE` default english; `TONE` is an enum (Objective by default).

## Model roles

Three model slots: `FAST_LLM` (cheap tasks), `SMART_LLM` (report writing), `STRATEGIC_LLM` (planning). Prometheus can map these to whatever models it has.

## Failure handling seen in the code

- Invalid retriever names fall back to the default retriever.
- LLM JSON often comes wrapped in code fences; the code repairs it (`json_repair`) instead of failing.
- URLs already scraped are tracked so repeats are not fetched twice.
- If supplied source URLs give nothing, a flag can add web search on top.
- MCP retrievers have strategies `fast` (reuse one result), `deep` (run per sub-query), `disabled`.

## Not verified

- Exact chunk size inside `select_context`.
- Behaviour of `detailed_report` merging beyond the subtopic count.
- Default models listed in `default.py` look like placeholders for newer models; treat them as examples.
