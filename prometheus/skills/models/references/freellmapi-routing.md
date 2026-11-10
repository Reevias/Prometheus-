# FreeLLMAPI: routing, quotas, failover, key storage

Source: https://github.com/tashfeenahmed/freellmapi (MIT, as of 2026-11-09)

Files read: `GLOSSARY.md`, `README.md`, `docs/en/architecture/01-routing-and-bandit-scoring.md`, `02-quota-and-cooldown-engine.md`, `04-degraded-mode-and-failover.md`, `server/src/providers/base.ts`, `server/src/lib/crypto.ts`.

## What it is

A self-hosted proxy (port 3001) that offers one OpenAI-style `/v1` endpoint. Model `auto` (or `auto:fast`, `auto:smart`, a profile name, or a model id) lets the router choose. Its disclaimer: for personal experiments, not production.

## Vocabulary

| Term | Meaning |
|---|---|
| Platform | An upstream such as groq or openrouter |
| Model row | One routable (platform, model id) with limits and ranks |
| Key | One stored credential; a model routes only if it has a healthy key under its limits |
| Chain | Ordered list of models tried for one request |
| Pool | Shared quota bucket (for example `openrouter::free`) |

## How a model is picked

1. Build the chain: active profile, else global fallback list, else an `auto:` sort name.
2. Score each model: `base = w_rel x reliability + w_speed x speed + w_intel x intelligence`.
3. Multiply by guardrails: `effective = base x headroom factor x rate-limit factor`.
4. Pick the best (not round-robin).

| Strategy | Reliability | Speed | Intelligence |
|---|---|---|---|
| balanced (default) | 0.50 | 0.25 | 0.25 |
| smartest | 0.35 | 0.10 | 0.55 |
| fastest | 0.35 | 0.55 | 0.10 |
| reliable | 0.70 | 0.15 | 0.15 |

- Reliability: a Beta distribution from recent successes and failures (7-day window, 2-day half-life), sampled randomly (Thompson sampling) so uncertain models still get tried.
- Speed: 60 percent throughput (tokens per second, saturating near 60) plus 40 percent time to first byte (300 ms best, 5 s worst). No data gives 0.6.
- Intelligence: tier first (Frontier 4, Large 3, Medium 2, Small 1) then rank.
- Headroom: when under 20 percent of quota remains, the score ramps down to a floor of 10 percent. Hard checks still block at 100 percent.
- Sticky session: same first user message keeps the same model for 30 minutes.

## Quota tracking

- Four windows per (platform, model, key): RPM and TPM (sliding 60 s), RPD and TPD (reset at UTC midnight).
- In-flight leases count requests that have started, so parallel calls cannot all pass the same check.
- Account-wide pools (OpenRouter daily requests, Cloudflare neurons, NVIDIA 40 RPM) are summed across models.
- Real limits are learned from provider error bodies when the list is wrong.

## Failover

1. Up to 20 attempts, within a 45 s time budget (first retry always allowed). A stalled first byte cancels the attempt.
2. Failure handling:

| Error | Skip | Bench |
|---|---|---|
| 401 bad key | key | 5 min |
| 402 no credit | key | 24 h |
| 403 model not allowed | model | 24 h |
| 429 daily used up | model and key | until UTC midnight or Retry-After |
| 429 per-minute | key | about 90 s |
| 5xx, timeout | platform | about 90 s |

3. Repeat hits climb a ladder: 2 min, 10 min, 1 h, 24 h. A provider-stated wait (header `Retry-After` or a delay in the error body such as "try again in 17s") wins, capped at 24 h.
4. Three failures across keys within 15 minutes bench the model on all keys for 10 minutes.
5. Degraded mode: if fewer than half of providers have a usable key for 60 s (at least 3 providers), exploration stops and the best remaining are used; it exits after 120 s of recovery.

## Key storage

Keys are encrypted with AES-256-GCM (random 16-byte IV, auth tag stored). The 32-byte master key comes from `ENCRYPTION_KEY` (64 hex characters; required in production). Outside production a key file `.encryption-key` is created next to the database with owner-only permissions, kept apart from the database itself. Losing the master key makes stored keys unreadable.

## Health checks

Every ~5 minutes each key is validated. Never validate with an endpoint that answers 200 without a key. Avoid probes that use paid quota.

## Not verified

- Behaviour of the hosted signed model catalog (premium tier).
- Exact retry code paths in `fallback-loop.ts` beyond the documented table.
