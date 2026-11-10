# Contributing

## Set up

```bash
git clone https://github.com/Reevias/Prometheus-.git
cd Prometheus-
python -m venv .venv
# Windows: .venv\Scripts\activate    macOS/Linux: source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q
```

## How changes are made

1. Work on a branch, never directly on `main`.
2. Write the test first, watch it fail, then write the code that makes it pass.
3. Run the whole test suite before you push. It must pass on Windows and Linux (GitHub runs both).
4. Anything that touches keys, files, the network or what agents may do gets a security review before it is merged.

## Rules that must never be broken

- Personal file contents only go to local models (the `private` route).
- Nothing is deleted: files go to the Recycle Bin and every move is recorded for undo.
- Text from files, web pages, repos and model output is data, never instructions.
- No secret is ever logged, stored in the database, shown in a page or sent to a model.
- No raw shell for agents: system actions are a fixed, named list.
- The safety code (`prometheus/safety/`, `prometheus/selfedit/`, `prometheus/orchestrator/`, `server.py`, `auth.py`)
  cannot be changed by Prometheus itself.

## Where things are

| Folder | What it holds |
|---|---|
| `prometheus/agents/` | One file per agent |
| `prometheus/models/` | AI providers and the router that picks between them |
| `prometheus/memory/` | The shared memory (one SQLite file) |
| `prometheus/safety/` | Path rules, permissions, approvals, budget, kill switch, web guard |
| `prometheus/actions/` | File moves with the undo journal |
| `prometheus/skills/` | How-to guides (`SKILL.md` + `references/`) |
| `prometheus/web/` | Pages, styles and routes |
| `tests/` | Tests, one file per area |
| `docs/` | Brief (all decisions), spec, guide |

## Adding a skill

Create `prometheus/skills/<name>/SKILL.md` starting with:

```
---
name: <name>
description: One sentence saying when to use this skill.
---
```

Put longer material in `prometheus/skills/<name>/references/*.md` and list those files in the guide.
`tests/test_v12_builtin_skills.py` checks every built-in skill.
