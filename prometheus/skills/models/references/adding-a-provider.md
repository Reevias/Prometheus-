# Adding a provider

Source: https://github.com/tashfeenahmed/freellmapi (MIT, as of 2026-11-09)

Files read: `server/src/providers/base.ts`, `server/src/providers/openai-compat.ts`, `server/src/providers/index.ts`, `server/src/lib/credential.ts`, `docs/en/providers/03-adding-a-new-provider.md`.

## What an OpenAI-compatible provider needs

| Item | Detail |
|---|---|
| Base URL | Ends before the path, for example `https://api.groq.com/openai/v1` |
| Chat path | The client posts to `<baseUrl>/chat/completions` |
| Auth header | `Authorization: Bearer <key>` |
| Model id | The string the provider uses, sent in the request body as `model` |
| Model list | `GET <baseUrl>/models` (used to check the key and list models) |
| Limits | RPM, RPD, TPM, TPD if published; use null/unknown if not |

Request body is the usual OpenAI shape: `model`, `messages`, optional `temperature`, `max_tokens`, `tools`, `stream`.

## Steps

1. Record the facts you can prove: free or trial, card needed, sign-up walls, region limits, training on prompts.
2. Pick the adapter. If the provider speaks the OpenAI wire format, one generic adapter plus a base URL is enough. If not (Gemini native format, queue-based services like AI Horde), write a small adapter class implementing `chatCompletion`, `streamChatCompletion` and `validateKey`.
3. Register options as needed:
   - `timeoutMs`: default 15 s; slow reasoning or cold-start models need 60-180 s.
   - `extraHeaders`: extra required headers (for example a referer and title for OpenRouter).
   - `validateUrl`: a different URL for the key check.
   - `keyless: true`: no key needed.
   - `forceSingleToolCall`: when the provider rejects parallel tool calls (NVIDIA).
4. Validate the key with a request that really needs the key. A public `/models` list that answers 200 to anyone proves nothing (Pollinations and ModelScope behave this way).
5. Do not spend metered quota on health checks, and do not send malformed requests to billed endpoints repeatedly; some providers treat that as abuse.
6. Add model rows only after a live check. Do not copy ids from lists unverified.
7. Add tests that check the URL, the auth header and response handling.

## How keyless providers work

- Marked `keyless: true` (Kilo gateway and OVHcloud are examples).
- The system stores a placeholder value `no-key`. When the stored value is empty or `no-key`, it sends no `Authorization` header at all. Sending `Bearer no-key` would be read as an invalid key.
- If a real key is saved for a keyless platform, it is used instead, because those providers accept both modes (higher limits with a key).
- Kilo's key check calls `/api/gateway/models` because `/v1/models` accepts only POST there.
- Limits for keyless access are by IP address (Kilo 200 per hour, OVH 2 RPM per model), so keep traffic low.

## Error handling every adapter shares

- Errors carry the HTTP status and any stated wait time (`Retry-After` header, or a delay inside the error body), capped at 24 hours.
- Some providers answer 200 but put an out-of-credits notice in the reply text; detect and treat it as a payment error so the router moves on.
- Strict providers (Mistral, Groq, Cerebras) reject unknown fields, so extra fields in messages are stripped before sending.
- Inline tool calls written as plain text are repaired into proper tool calls when possible.

## Safety

Keys are encrypted at rest; never print them in logs or errors. Check the provider's terms for personal proxy use.

## Not verified

- Whether every provider in the awesome list follows the shape above.
- The hosted catalog rules for model ids and age gates.
