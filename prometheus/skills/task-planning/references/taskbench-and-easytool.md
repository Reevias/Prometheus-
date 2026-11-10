# TaskBench and EasyTool

Source: https://github.com/microsoft/JARVIS (MIT, as of 2025-07-29)

Files read: `taskbench/README.md`, `taskbench/evaluate.py`, `easytool/README.md`, `easytool/easytool/toolbench.py`, `easytool/data_funcqa/tool_instruction/functions_data.json`.

## TaskBench: how plans are scored

TaskBench splits task automation into three stages and scores each.

| Stage | What is compared | Metrics |
|---|---|---|
| Task decomposition | The written task steps | Rouge-1, Rouge-2, BERTScore F1 |
| Tool invocation | Tools chosen (nodes) and dependencies (edges) | node F1 (n-F1), edge F1 (e-F1) |
| Parameter prediction | Argument names, then argument values | name F1 (t-F1), value F1 (v-F1) |

`evaluate.py` also reports edit distance and link metrics. Predicted and true graphs are matched with an assignment cost (`cost = 2 - node similarity - 0.5 * link similarity`), so a plan is not punished just for numbering tasks differently. Names are compared after replacing "_" with spaces.

**Plan shapes.** Samples are one of three: a single node, a chain, or a DAG (branching graph). Easy plans are chains; the hard part is getting edges right.

**Tool library format.** Each tool is JSON: `id`, `desc`, and either `input-type` / `output-type` lists (resource dependency: a tool can follow another if types fit) or a `parameters` list of `{name, type, desc}` (temporal dependency, for APIs like `send_sms`).

**How the data was made (Back-Instruct).**
1. Build a tool graph from the tool library (`generate_graph.py`).
2. Sample sub-graphs: node, chain or DAG (`graph_sampler.py`).
3. Have an LLM write the steps and the user instruction from the sub-graph, then fill in the arguments (`data_engine.py`).
4. Check with an LLM critic and a rule critic comparing the plan's nodes and edges to the sampled graph.
5. Humans verify; about 10-13 percent of checked samples were discarded.

Use for Prometheus: build a small set of test requests with known correct plans, and score tool choice, dependency edges and arguments separately.

## EasyTool: concise tool descriptions

Problem: raw tool documentation is long, inconsistent and has no examples, so agents pick the wrong tool or give bad arguments. EasyTool rewrites each tool into one short, uniform record.

Record shape (from `functions_data.json`):

| Field | Content |
|---|---|
| `ID` | Number used for selection |
| `standardized_name` | Clean name, e.g. `add_` |
| `API_description` | One or two sentences: what it returns and how, e.g. "returns the sum of all the arguments, normalized to 2 decimal places" |
| `Usage.required_parameters` | `name` and `type` for each |
| `Usage.Example` | A `Scenario` ("if you want to add 2 to 1") and sample `Parameters` |

For larger tools (`toolbench_tool_instruction.json`) the description lists the tool's APIs, each named and explained in a sentence.

**Writing rules (inferred from the data):** one purpose per sentence; say what is returned; name every parameter with its type; give one realistic example; drop marketing text and repeated detail.

## EasyTool's selection flow

1. `choose_tool`: show the question and the numbered tool list; must pick ONE and answer only as JSON like `{"ID": 3}`.
2. `choose_parameter`: show that tool's documentation and the question; output the parameters as JSON.
3. Call the tool; then `answer_generation` turns the call result into an answer. A `_depend` variant also sees earlier steps' logs.
4. Retry rules: if JSON parsing fails, ask again, up to about 10 times, then return empty.

## Not verified

- The exact prompt that generated EasyTool's descriptions (the repo ships the finished descriptions, not the generator prompt).
- Leaderboard numbers.
