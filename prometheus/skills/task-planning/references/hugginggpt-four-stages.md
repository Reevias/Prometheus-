# HuggingGPT: the four stages

Source: https://github.com/microsoft/JARVIS (MIT, as of 2025-07-29)

Files read: `hugginggpt/server/awesome_chat.py`, `hugginggpt/server/configs/config.default.yaml`, `hugginggpt/server/demos/*.json`. The main entry is `chat_huggingface()`.

## Stage 1: task planning (`parse_task`)

The model gets a system prompt, a few worked examples from `demos/demo_parse_task.json` (user request, then the JSON plan), the chat history, and the new request. It must output a JSON list:

`[{"task": "...", "id": 0, "dep": [-1], "args": {"text": "...", "image": "...", "audio": "..."}}]`

| Field | Meaning |
|---|---|
| `task` | One name from a fixed list (for example `image-to-text`, `object-detection`, `visual-question-answering`, `text-to-speech`) |
| `id` | Number, increasing from 0 |
| `dep` | Ids of earlier tasks whose output this task needs; `-1` means none |
| `args` | Only the keys `text`, `image`, `audio` |

**Placeholder convention:** when an input is another task's output, write `<GENERATED>-<id>` and put that id in `dep`. Example: task 2 `args.image = "<GENERATED>-1"`, `dep: [1]`.

Prompt intent: think step by step; produce as few tasks as possible; watch input and output types and ordering; if the request cannot be parsed, return `[]`. Temperature is 0. Chat history is trimmed from the oldest turns until the prompt fits with about 800 tokens to spare.

**Clean-up after parsing**
1. `unfold()` splits a task whose placeholder lists several sources (`<GENERATED>-1,<GENERATED>-2`) into one task per source.
2. `fix_dep()` rebuilds `dep` from the placeholders actually present, so a wrong `dep` is corrected. No placeholder gives `[-1]`.
3. In the run loop, a dep that points to an id equal or greater than the task's own id is dropped (set to `[-1]`), which blocks forward references.

## Stage 2: model selection (`choose_model`)

Per task: take up to 10 candidate models for that task type (`MODELS_MAP`), check which are loaded and reachable (a thread per model, local and hosted), and keep up to `num_candidate_models` (default 5).
- 0 available: the task fails with an error.
- 1 available: use it ("Only one model available").
- Several: send the model a short list with `id`, endpoint, `likes`, a description cut to `max_description_length` (100 chars) and tags. It must answer `{"id": "...", "reason": "..."}`. The prompt says to prefer models with local endpoints for speed and stability. If the reply is not valid JSON, regexes pull out id and reason.

Special cases: pure text tasks (summarization, translation, conversational, text-generation, text2text-generation) go straight to the main chat model. ControlNet-style tasks use a fixed local model. A "-text-to-image" task with no text is downgraded to the plain control task.

## Stage 3: task execution (`run_task`)

Loop: start a thread for every task whose deps are all in the results dict `d`; sleep 0.5 s when nothing new can start; give up after 160 idle rounds (about 80 s). Before running a task, placeholders in `args` are replaced with the dependency's real output (`generated text`, `generated image`, `generated audio`); local file names get a `public/` prefix. Each result stores the task, the chosen model and reason, and the inference result.

## Stage 4: response generation (`response_results`)

All results, sorted by id, go to the model with the prompt idea: answer the request directly from the results first; some results may be wrong, so judge carefully; then describe the workflow, models used and results in a friendly tone; drop irrelevant information; give full file paths or URLs; if nothing came back, say you could not do it.

## Failure cases handled

| Case | Handling |
|---|---|
| Plan is not valid JSON, or is `[]` | Fall back to normal chat |
| Single plain-text task | Treated as chat |
| Task type unknown | Result error "not found in available tasks" |
| No model available | Result error; other tasks continue |
| Inference error | Error stored as that task's result; reported in stage 4 |
| Bad choose-model JSON | Regex rescue |
| Cycle or never-ready deps | Run loop times out |

Every failure is appended to `logs/log_fail.jsonl`, successes to `log_success.jsonl`.

## Config names

`model`, `inference_mode` (local, huggingface, hybrid), `local_deployment` (minimal, standard, full), `num_candidate_models`, `max_description_length`, `logit_bias` per stage, and the `tprompt`, `prompt`, `demos_or_presteps` keys for each stage.

## Not verified

- The model-download or ranking logic behind `MODELS_MAP`.
- Behaviour of the web client.
- Whether failed deps stop dependants (the code seems to leave them waiting until timeout).
