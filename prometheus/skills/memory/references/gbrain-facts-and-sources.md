# Facts, pages, provenance, corrections

Source: https://github.com/garrytan/gbrain (MIT, as of 2026-11-10)

Files read: `README.md`, `docs/protocol/MEMORY_VERBS_v1.md`, `docs/takes-vs-facts.md`, `docs/guides/{page-anatomy,compiled-truth,source-attribution,entity-scoped-facts,memory-trust}.md`. DESIGN.md is a UI style guide and has nothing on memory.

## Two kinds of stored knowledge

| Layer | What it holds | Written by |
|---|---|---|
| Pages | Markdown files, one per person, company, idea. Frontmatter (`type`, `title`, `tags`, `aliases`, `visibility`), a "compiled truth" part that is rewritten as evidence changes, a `<!-- timeline -->` line, then an append-only dated timeline | Human or agent (`put_page`) |
| Facts (hot) | Single claims saved from conversation. Kinds: `event`, `preference`, `commitment`, `belief`, `fact` | `remember` |
| Takes (cold) | Who believes what, with a weight from 0 to 1. Kinds: take, fact, bet, hunch | Extracted from pages |

Do not mix them: other people's opinions are takes, not the owner's facts. A nightly "consolidate" step promotes facts to takes in one direction only. Prometheus can start with pages plus facts and skip takes.

## The seven memory verbs (frozen protocol)

`recall`, `remember`, `entity`, `synthesize`, `forget`, plus `context_pack` and `delta`.

### remember(fact, provenance, ...)
1. `provenance` is required free text, up to 500 characters, kept as written. Empty is refused.
2. `entity` names the page the fact is about. Without it recall by entity will not find the fact. If omitted, the system may link it only when the text names exactly one entity page; anything ambiguous stays unattributed.
3. Optional: `ttl` (`30d`, `12h`, or a timestamp; default never expires), `kind`, `valid_from`, `visibility` (`world` readable by all connected agents, or `private`).
4. The result `status` is `inserted`, `duplicate` or `superseded`. Branch on the status code, not the message.
5. Supersession rule: same entity, same kind, high similarity, different text means the new fact replaces the old. Without an embedding model, duplicate checks degrade and near-duplicates may be inserted.

### Corrections and replacement
- `replaces: <fact id>` supersedes a specific fact. The old one is expired and marked `superseded by #N`.
- Refusals carry codes: `target_withdrawn`, `target_superseded`, `target_expired`, `replaces_entity_mismatch`, `replaces_cross_page`.

### forget(id, reason)
- Expires a fact by id. It does not delete; there is an audit trail, and repeating it is safe.
- A forgotten claim cannot be restored by saying the same text again (`fact_withdrawn` refusal).
- A fact with no entity withdraws the claim for every entity. Forgetting it is refused while entity-linked copies exist (`claim_linked`).
- In the file view the row is struck through with a `valid_until` date and `forgotten: <reason>`.
- Real deletion of the text is a separate owner-only command that is never available over the agent connection.

## Provenance and citations

Every fact and every timeline line carries a citation: who, in what channel, about what, and when. Example shape: `[Source: email from Alice re Q2 deck, 2026-04-05 2:30 PM PT]`. Rules:
1. "User said" alone is not enough; add where and when.
2. Social posts need a full URL.
3. Compiled-truth text needs citations too, not only the timeline.
4. When sources conflict, write both and do not pick silently.
5. Authority order for conflicts: owner's direct statements, then primary sources (meetings, email), then enrichment APIs, then web search, then social media.

## Trust labels

Every item has a tier shown on reads: `user_confirmed`, `operator_curated` (the owner's own notes), `tool_observed`, `agent_written`, `unknown`, `external_untrusted`. Defaults: labels always on; instruction-like writes ("ignore previous instructions", "from now on always...") are saved but flagged. Optional stricter modes hold them in quarantine. Confirmation needs the owner typing a token at a terminal.

## Entity-scoped facts

When the same question has different answers per client, save each fact with its entity and recall per entity. When no entity is named, return rows with their entity labels and ask which one is meant; never answer one client with another's row. A default rule goes on its own page and is checked second.

## Not verified

- Exact limits on page size and database storage.
- How the "synthesize" verb composes answers; it needs a chat model.
- Takes extraction quality figures were seen but not re-checked.
