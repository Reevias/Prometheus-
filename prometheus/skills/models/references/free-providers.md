# Free LLM providers

Source: https://github.com/mnfst/awesome-free-llm-apis (CC0, as of 2026-11-04; `data.json` lastUpdated 2026-10-25)

Files read: `data.json` (providers, footnotes, glossary). Limits and models change often; confirm on the provider site before relying on them. RPM = requests per minute, RPD = per day, TPM/TPD = tokens per minute/day.

## All providers in the list

| Provider | Base URL | Key? | Sign-up | Notable free models | Limits |
|---|---|---|---|---|---|
| Aion Labs | `https://api.aionlabs.ai/v1` | Yes | aionlabs.ai/app/api-keys | aion-3.0, aion-3.0-mini (roleplay-tuned) | 15 RPM, 20K TPD |
| Cohere | `https://api.cohere.com/v2` | Yes (trial key) | dashboard.cohere.com/api-keys | Command A+, Command A, Command R+ | 20 RPM, 1,000 calls/month, non-commercial |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta` (native Gemini format) | Yes | aistudio.google.com/app/apikey | gemini-3.8-flash, 3.6-flash, flash-lite models | Not published; see AI Studio |
| Mistral AI | `https://api.mistral.ai/v1` | Yes | console.mistral.ai/api-keys | mistral-medium-3-5, small, large, codestral | about 1 RPS, 500K TPM (last published) |
| Z AI (Zhipu) | `https://open.bigmodel.cn/api/paas/v4` (international: `https://api.z.ai/api/paas/v4`) | Yes | open.bigmodel.cn/usercenter/apikeys | glm-4.7-flash, glm-4.6v-flash | 1 concurrent request |
| Cloudflare Workers AI | `https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run` | Yes (token + account id) | dash.cloudflare.com/profile/api-tokens | llama-3.3-70b, gpt-oss-120b, gemma-4 | 10,000 neurons/day shared |
| Gonka Broker | `https://proxy.gonkabroker.com/v1` | Yes | app.gonkabroker.com/signup | MiniMax-M2.7, DeepSeek-V4-Flash, bge-m3 (embeddings) | about 1M tokens/month shared |
| Groq | `https://api.groq.com/openai/v1` | Yes | console.groq.com/keys | gpt-oss-120b, gpt-oss-20b, qwen3.8-27b | 30 RPM, 1,000 RPD |
| Kilo Code | `https://api.kilo.ai/api/gateway` | **No** | app.kilo.ai/profile (optional) | `kilo-auto/free` router, nemotron, `:free` models | 200 requests/hour per IP |
| LLM7.io | `https://api.llm7.io/v1` | **Optional** | token.llm7.io | gpt-oss:20b, codestral-latest | Free token: 60 RPM, 250/hour, 100K tokens/day |
| ModelScope | `https://api-inference.modelscope.cn/v1` | Yes | modelscope.cn/my/myaccesstoken | Qwen3.5-35B-A3B, Qwen3.5-27B | 2,000 RPD total, 500 per model |
| NVIDIA NIM | `https://integrate.api.nvidia.com/v1` | Yes | build.nvidia.com | gemma-4-31b-it, nemotron-3-ultra, gpt-oss-20b | 40 RPM, 10,000 RPD |
| OpenRouter | `https://openrouter.ai/api/v1` | Yes | openrouter.ai/keys | models with `:free` suffix, `openrouter/free` router | 20 RPM, 50 RPD for all free models together |
| OVHcloud AI Endpoints | `https://oai.endpoints.kepler.ai.cloud.ovh.net/v1` | **No** (anonymous) | ovhcloud.com (catalog page) | Qwen3.5-397B, gpt-oss-120b, Llama-3.3-70B | 2 RPM per IP per model |
| SiliconFlow | `https://api.siliconflow.cn/v1` | Yes | cloud.siliconflow.cn/account/ak | Hunyuan-MT-7B and a few others | Not stated |

## Keyless endpoints

Kilo Code (200/hour per IP), OVHcloud (2 RPM per IP per model, EU hosted), LLM7.io (anonymous reaches `turbo` models; last confirmed 2026-08-21). A request to these must send no `Authorization` header.

## Privacy and access caveats (from the footnotes)

| Provider | Caveat |
|---|---|
| Google Gemini | Free-tier prompts may be used to improve Google products, except in the EEA, UK and Switzerland |
| Mistral | Free mode prompts may be used to train models unless you opt out |
| OpenRouter | Free providers may log prompts for training |
| Kilo Code | Free pool may route to providers that log prompts; some may keep them for training |
| NVIDIA (free endpoints) | "Trial use only - do not submit personal or confidential data"; usage is logged |
| Cohere | Trial key: non-commercial use only |
| ModelScope | Needs Alibaba Cloud binding and real-name verification; untestable outside mainland China |
| SiliconFlow | Real-name identity verification needed; untestable outside China |
| Gonka Broker | Phone (SMS) verification with a real number; one grant per number |
| Cloudflare | Seven large models are excluded from the free allowance; over quota the request fails, no bill |
| LLM7.io | Free quota may be reduced without notice |

Not stated in the list for Groq, Aion Labs, Z AI, OVHcloud: treat as "unknown, assume logged".

## Planned shutdowns noted

Gemini 3.1-flash-lite ends 2027-05-07; Groq retired some Qwen and compound models in September 2026; GLM-4.5-Flash is announced for retirement; two OVH Mistral models end 2026-11-04.

## Use for Prometheus

Private notes: local model only. Public questions: any row above. Many providers offer an OpenAI-style `/chat/completions` path, but the list says native and compatible URLs can differ, so check each.

## Not verified

- Whether each base URL accepts the standard OpenAI request shape (read from the list's wording, not tested).
- Live availability; nothing was called.
