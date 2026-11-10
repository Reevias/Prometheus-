# Deep research loop (breadth and depth)

Source: https://github.com/dzhng/deep-research (MIT, as of 2026-04-11); a Python port of the idea is in https://github.com/assafelovic/gpt-researcher `gpt_researcher/skills/deep_research.py` (Apache-2.0, as of 2026-11-09)

Files read: `src/deep-research.ts`, `src/prompt.ts`, `src/feedback.ts`, `src/run.ts`.

## Inputs

| Name | Default | Meaning |
|---|---|---|
| breadth | 4 (asked at start; suggested 2-10) | How many search queries per level |
| depth | 2 (suggested 1-5) | How many levels of follow-up |
| `FIRECRAWL_CONCURRENCY` | 2 | Parallel searches |

GPT Researcher's port uses `DEEP_RESEARCH_BREADTH` 3, `DEEP_RESEARCH_DEPTH` 2, `DEEP_RESEARCH_CONCURRENCY` 4.

## Step 0: clarify (feedback.ts)

`generateFeedback()` asks the model for up to 3 follow-up questions to sharpen the request. The user answers; the initial query, questions and answers are merged into one combined query. Skipped when the user wants a short exact answer.

## The loop: `deepResearch({query, breadth, depth, learnings, visitedUrls})`

1. `generateSerpQueries()` makes up to `breadth` queries. Each has a `query` and a `researchGoal` (what this query is for and which directions to follow next). Known learnings are passed in so new queries are more specific. The prompt allows fewer queries if the prompt is already clear and demands they are not similar.
2. Run searches in parallel, limited by the concurrency value. Each search takes the top 5 results with page text in markdown, 15 second timeout.
3. `processSerpResult()` trims each page to 25,000 characters and asks the model for up to 3 **learnings** and up to `numFollowUpQuestions` follow-up questions. A learning must be short but dense: include people, companies, products, exact numbers and dates. 60 second timeout.
4. Append the new learnings and URLs to the running lists.
5. If `depth - 1 > 0`: build the next query from "Previous research goal" plus the follow-up questions, and call `deepResearch` again with `breadth = ceil(breadth / 2)` and `depth - 1`.
6. If depth is used up, return the learnings and URLs.
7. Merge all branches and remove duplicates with a set.

## How breadth shrinks

Each level halves breadth (rounded up). Breadth 4, depth 3 gives 4 queries, then 2 per branch, then 1 per branch. The Python port uses `max(2, breadth // 2)` so it never drops below 2.

## Failure handling

A failed or timed-out query logs the error and returns empty learnings, so one bad branch does not stop the rest. The Python port also stops descending if a level produced zero queries or zero successful results, to avoid endless work, and trims context to 25,000 words (`MAX_CONTEXT_WORDS`), keeping the newest items.

## Final output

- `writeFinalReport()`: gives the model the original prompt and all learnings, asks for a detailed report of 3 or more pages that includes all learnings, then appends a "## Sources" list of visited URLs.
- `writeFinalAnswer()`: for short questions; returns only the exact answer in the format the prompt asks for.

## Intent of the system prompt (prompt.ts)

Tell the model today's date; accept news newer than its training; treat the reader as an expert; be organised and accurate; judge arguments over authority; allow speculation if flagged. For Prometheus, keep the date, accuracy and "flag speculation" lines; adjust the "expert" tone to suit a beginner reader.

## Not verified

- The TypeScript version cites URLs only as a list, not per claim; the Python port tracks a citation per learning.
- Token limits of the model helper `trimPrompt` were not read.
