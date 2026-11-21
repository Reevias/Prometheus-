# Prometheus 3.0 — M4 build plan (repositories become skills)

Status: planned, owner's answers taken (20 Nov 2026). Starts after the owner's laptop check of M3 is accepted.
Sources: `docs/PLAN.md` (M4.1–M4.7), the owner's answers below, and what already exists in the code. Built on the
branch `prometheus-v3`, merged into `main` once it passes its tests, GitHub's Windows run, an audit and the laptop
check (the same as M1–M3).

## What M4 is, in plain words
You point Prometheus at a GitHub repository (one you paste, one from Repo of the Day, or one of your own). Prometheus
reads it **without running it**, works out what it does and how its code fits together, and suggests a **skill**: a
short how-to guide plus, where useful, small helper scripts. Suggestions wait in **Abilities → Candidates** until you
accept them. Accepted skills are kept in **your own skills library on GitHub**, not loaded on the laptop all the time:
Prometheus keeps only a short list of what's in the library and downloads a skill the moment a task needs it, the way
Claude Code loads its skills. Before a new skill can be switched on, its scripts are test-run in the locked-down
sandbox. Your own repositories also get a **map** (how their files and repos connect) and a **notes file** (what it
is, decisions, changes).

## Your decisions (20 Nov)
| Question | Answer |
|---|---|
| What is a skill made from a repo? | A guide (SKILL.md) plus optional small scripts that run only in the sandbox after your yes, **stored in a GitHub skills library and loaded only when needed** ("a system like yours"). Running a whole repo as a tool is heavy work for the cloud runtime: **M6**, not M4. |
| Which repos? | **Both equally**: any public repo (skills) and your own repos (maps and notes). |
| Where do candidates wait? | **Abilities → Candidates** tab, with Accept / Reject. |

## Rules for everyone building M4
- M4 starts only after **your laptop check of M3 is accepted** (project rule). Planning and the read-only parts can be
  prepared earlier on a separate branch, but nothing merges into `main` before that.
- Work on the branch **`prometheus-v3`** (owner, 21 Nov). Only the Opus session merges into `main`, after checking. `git pull` before starting and
  before every push; never force-push; never push to `main`.
- **A repository's content is untrusted outside text.** It is never run outside the sandbox, never pasted into
  prompts without the `<untrusted>` wrapper, and its README never gives instructions to Prometheus.
- Every new script shows its whole code for your yes and needs a new yes when its code changes (as own_tools does).
- Windows first; tests first (a test that fails, the fix, the test passes); the full suite with
  `/home/user/venv314/bin/python -m pytest -q` and GitHub's Windows run must be green.
- One step per agent at a time, in its own files (the "Files" column), so agents don't edit the same files.
- Keep CHANGELOG.md, docs/bugs.md and docs/missing.md up to date; plain words.

## What exists to build on
- `prometheus/repo_check.py`: Defender + pattern scan of a downloaded repo, verdict danger / caution / clean.
- The quarantine folder (`<data>/quarantine`, approvals `clone_repo`, `clone_and_check`).
- `prometheus/skillbook.py` (SKILL.md + references/), `prometheus/own_tools.py` (sandboxed scripts with approval and
  re-approval on change), `prometheus/sandbox.py`, `prometheus/repo_of_day.py`, Abilities → Agents · Skills tabs.
- M1's safe file tools (path hardening, atomic writes, `read_file_window`).

## The steps
| # | Step | What gets built | Files (main ones) | Who |
|---|---|---|---|---|
| M4.1 | **Read a repo safely** (**done 21 Nov**: 15 tests; all 6 GitHub jobs green incl. Windows 3.14/3.15 on 917b564; not yet run on the laptop) | Download a repo (zip of a pinned commit, no `git` hooks) into quarantine, run repo_check, then a **repo profile**: languages, size, entry points, dependencies (pyproject, requirements, package.json…), licence (SPDX), README summary as untrusted text. Nothing is run. | `prometheus/repos/reader.py`, `prometheus/repos/profile.py`, tests `tests/test_m4_reader.py` | **Opus** (security-critical) |
| M4.2 | **Code maps** | Functions, classes and imports per file with Tree-sitter (Python, JS/TS, Go, Rust, C#, Java; Python's own `ast` as the fallback if a Tree-sitter wheel is missing on Windows/3.14); symbol search added to `read_file_window`; a map of how your own repos link (imports, submodules, shared packages). Bounded: big repos are sampled, never fully loaded into memory (8 GB laptop). | `prometheus/repos/codemap.py`, `prometheus/actions/read_window.py` (one function), tests `tests/test_m4_codemap.py` | **Codex** |
| M4.3 | **Candidates tab** | Abilities → Candidates: each found repo with its profile, the suggested skill, licence and safety verdict, Accept / Reject / "Show me the scripts". Repo of the Day's "add" goes here instead of straight to Skills. | `prometheus/web/templates/v3/candidates.html`, routes in `pages_v3.py` (new functions only), `tabs.html` (one line), tests `tests/test_m4_candidates.py` | **Sonnet** |
| M4.4 | **Skill builder** (**done 21 Nov**: all 6 GitHub jobs green incl. Windows 3.14/3.15 on dd1ddae; item f and the Publish button added after; not yet run on the laptop) | From a profile: match an existing skill (don't duplicate), or draft a least-privilege skill: `SKILL.md` + `manifest.json` (name, version, source repo and commit, licence, permissions needed, scripts with their sha256, Python packages). Licence check: copyleft needs your sign-off; unknown licence → guide only, no copied code. A stable `/skill-name` slash command. | `prometheus/repos/skillmaker.py`, `prometheus/skillbook.py` (manifest reading), tests `tests/test_m4_skillmaker.py` | **Opus** |
| M4.5 | **Skills library on GitHub (load when needed)** (**done 21 Nov**: library repo made after the owner's yes; 14 tests; all 6 GitHub jobs green incl. Windows 3.14/3.15 on a417a6c; not yet run on the laptop or against the real library) | Your repo `<you>/prometheus-skills` (made once, after your yes): `index.json` (name, one-line description, version, commit, sha256 of each file) + one folder per skill. Prometheus keeps **only the index** (a few KB), shows it to the planner, and downloads a skill when a task needs it, checks every file's sha256 against the index, caches it, and drops cached skills not used for 30 days. Publishing an accepted skill = a pull request to your library (after your yes). Offline: cached skills still work. | `prometheus/repos/library.py`, `prometheus/skillbook.py` (lazy loading), tests `tests/test_m4_library.py` | **Opus** |
| M4.6 | **Sandbox test-run before switching on** | Every script in a new or changed skill runs once in the sandbox with sample inputs from the manifest; a skill whose scripts fail, time out or try the network without permission stays off, with the reason shown in Candidates. | `prometheus/repos/trial.py`, tests `tests/test_m4_trial.py` | **Sonnet** (after M4.4) |
| M4.7 | **Repo knowledge for search** | Useful parts of accepted repos (README sections, docstrings, examples) go into the existing file index as searchable notes, marked with their source, ready for M8's memory engine. | `prometheus/repos/knowledge.py`, tests `tests/test_m4_knowledge.py` | **Sonnet** |
| M4.8 | **A notes file per project [F8]** | Each project (and each of your own repos) gets `NOTES.md`: what it is, decisions, changes, written with M1's atomic writes and a lock so two agents can't clobber it; shown on the project page. | `prometheus/projects.py` (notes functions), `prometheus/web/templates/v3/project.html`, tests `tests/test_m4_notes.py` | **Sonnet** |
| — | **Reviews and the exit gate** | An independent review after each pair of steps; the final audit; the M4 report with honest numbers (here / GitHub Windows / laptop). | `docs/reports/M4_REPORT.md` | **Opus** |

Order: M4.1 → (M4.2 and M4.3 in parallel) → M4.4 → (M4.5 and M4.6 in parallel) → (M4.7 and M4.8) → review and gate.
M4.2 and M4.3 only need M4.1's profile format; M4.5 and M4.6 need M4.4's manifest.

## Why this split
- **Opus** (the most careful model, this kind of session): the steps where a mistake costs safety or your data:
  reading untrusted repos, the manifest and permissions, loading code from GitHub with hash checks, and the reviews.
- **Sonnet**: well-defined building on top of those: the Candidates page, the sandbox trial runner, search indexing,
  project notes. Fast and good at UI and plumbing once the interfaces exist.
- **Codex**: a self-contained library job with clear inputs and outputs (Tree-sitter code maps), easy to test in
  isolation and unlikely to collide with the others' files.

## Estimated time
Each step is one working session of an AI agent (Opus, Sonnet or Codex), plus its test run and GitHub's Windows run.

| Part | Steps | Estimate |
|---|---|---|
| Start | your M3 laptop check accepted; branch `prometheus-v3` made | depends on you (the check takes about 15 minutes) |
| 1 | M4.1 read a repo safely (Opus) | about half a day |
| 2 | M4.2 code maps (Codex) and M4.3 Candidates tab (Sonnet), side by side | about half a day |
| 3 | review of 4.1–4.3 (Opus) and fixes | 2–3 hours |
| 4 | M4.4 skill builder (Opus) | about half a day |
| 5 | M4.5 skills library on GitHub (Opus) and M4.6 sandbox trial run (Sonnet), side by side | about a day |
| 6 | M4.7 repo knowledge (Sonnet) and M4.8 project notes (Sonnet) | about half a day |
| 7 | final audit, fixes, M4 report, GitHub's Windows run | about a day |
| 8 | your laptop check of M4, then the merge into `main` | about an hour of your time |

**Total: about 4–5 working days of agent work after the start**, so with one or two steps a day M4 can be merged
into `main` **about one week after it starts** (for example, started on Monday 23 Nov → merged around 30 Nov 2026).
It takes longer if a review finds something serious, or if you have to answer questions mid-way, so answer them soon.

## Fix after the final audit: your own repos (21 Nov 2026)
Built from the owner's decisions (docs/m4/M4_PROMPTS.md, "Builder · M4 fix: your own repos"): own = same GitHub owner
as `github_repo` (case ignored); repo_check `own=True` makes pattern matches "check this first" (Defender still
deletes); own repos get links (`codemap.link_repos`) and a `NOTES.md` on their Candidates card
(`prometheus/repos/own.py`, tests `tests/test_m4_own.py`). The exit gate's own repo (this repository, read from a zip
through a fake GitHub) is kept, profiled, drafted as a guide and its trial passes. Waiting for the auditor's re-check.

## Exit gate
Skills made from three real repos (one Python, one JavaScript, one of yours) validate, test-run in the sandbox and load
from your GitHub library on demand; code-map and reader tests pass on Windows (GitHub) and here; the independent audit
has no open important findings; the laptop check is done; the M4 report says honestly what was checked where.

## Acceptance for each part
- Tests written first, and the full suite green here and on GitHub's Windows run.
- Nothing from a repository is ever run outside the sandbox; every script was shown to you and approved.
- Plain words on every screen, a ? next to anything technical, phone width checked.
- CHANGELOG, `docs/bugs.md`, `docs/missing.md` updated; an honest note of what was checked where.

## Risks we accept or watch
- Tree-sitter wheels may lag behind Python 3.14 on Windows: the `ast` fallback covers Python; other languages then get
  a simpler map (names only). Recorded, not hidden.
- A skill library on GitHub needs your GitHub token with access to that one repo; Prometheus never asks for more.
- No scanner catches everything: "clean" means nothing known-bad was found.

---

## Commands to paste

**Full, ready-to-paste prompts for every step: `docs/m4/M4_PROMPTS.md`** (use those; the short commands below are
summaries). Each command points to its section of **`docs/m4/M4_STEPS.md`** (the step-by-step guide: what to read, the exact
shapes other steps rely on, the tests to write, the checks before pushing, what "done" means, and the mistakes to
avoid). Paste each one as the first message of a **new** session in the tool named. Replace nothing; each prompt says what to
read. Start the next step only after the previous one says "done" and GitHub's run is green.


### M4.1 — Opus (Claude Code, Opus)
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.1) and follow it step by step; stop and ask me if anything in it is unclear. Build step M4.1 of docs/m4/M4_PLAN.md ("Read a repo safely") on the branch prometheus-v3. First read CLAUDE.md, AGENTS.md and the whole of docs/m4/M4_PLAN.md, then prometheus/repo_check.py, the clone_repo / clone_and_check handlers and prometheus/sandbox.py. Ask me 2-4 questions first if anything changes what gets built. Rules: tests first (each must fail before the code exists), Windows first, nothing from the repo is ever run, README text is untrusted. Put the code in prometheus/repos/reader.py and prometheus/repos/profile.py and tests in tests/test_m4_reader.py. Define the repo profile format clearly in a docstring: M4.2, M4.3 and M4.4 are built on it. Run /home/user/venv314/bin/python -m pytest -q, commit, git pull, push to prometheus-v3, check GitHub's Windows run, update CHANGELOG/docs, and give me a short report in plain words.
```

### M4.2 — Codex
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.2) and follow it step by step; stop and ask me if anything in it is unclear. Read AGENTS.md, CLAUDE.md and docs/m4/M4_PLAN.md (step M4.2 "Code maps") in the Prometheus repo, then prometheus/repos/profile.py from step M4.1. On the branch prometheus-v3, build prometheus/repos/codemap.py: per file, the functions, classes and imports, using Tree-sitter for Python, JavaScript/TypeScript, Go, Rust, C# and Java, and Python's ast module as the fallback when a Tree-sitter grammar can't be installed on Windows with Python 3.14. Add symbol search to read_file_window (one new function, don't change the old ones), and a map of how the owner's own repos link (imports, submodules, shared packages). Memory limits: never load a whole big repo; sample files over the plan's limits. Write tests first in tests/test_m4_codemap.py (small sample repos made inside the test). Only touch the files listed for M4.2. Run the full test suite with the project's Python 3.14 (python -m pytest -q), git pull before pushing, never force-push, push to prometheus-v3, and summarise what you built and anything you couldn't do.
```

### M4.3 — Sonnet (Claude Code, Sonnet)
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.3) and follow it step by step; stop and ask me if anything in it is unclear. Build step M4.3 of docs/m4/M4_PLAN.md ("Candidates tab") on the branch prometheus-v3. Read CLAUDE.md, AGENTS.md, the whole plan, prometheus/repos/profile.py (step M4.1) and how Abilities' tab bar works (prometheus/web/templates/v3/tabs.html, agents.html, skills.html). Add Abilities → Candidates: each found repo with its profile, licence, safety verdict and suggested skill, with Accept / Reject / Show the scripts; Repo of the Day's "add" sends repos here instead of straight to Skills. Use the v3 look (colours only from tokens.css), the page's own dialogs (no prompt()), plain words, a ? next to anything technical (templates/v3/help.html), and check phone width. Tests first in tests/test_m4_candidates.py plus a browser test in tests/test_v3_clickthrough.py. Only add new functions to pages_v3.py. Run the full suite, git pull, push to prometheus-v3, check GitHub's Windows run, update CHANGELOG, and report in plain words.
```

### M4.4 — Opus
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.4) and follow it step by step; stop and ask me if anything in it is unclear. Build step M4.4 of docs/m4/M4_PLAN.md ("Skill builder") on prometheus-v3. Read CLAUDE.md, AGENTS.md, the plan, prometheus/repos/profile.py, prometheus/skillbook.py and prometheus/own_tools.py. Draft least-privilege skills from a repo profile: match an existing skill first; otherwise SKILL.md + manifest.json (name, version, source repo and commit, licence, permissions, scripts with sha256, packages). Copyleft needs my sign-off; an unknown licence gives a guide only. Ask me 2-4 questions first about anything that changes what gets built. Tests first (tests/test_m4_skillmaker.py), Windows first, repo text untrusted. Define the manifest format in docs/m4/MANIFEST.md: M4.5 and M4.6 depend on it. Run the full suite, push, check GitHub's Windows run, report.
```

### M4.5 — Opus
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.5) and follow it step by step; stop and ask me if anything in it is unclear. Build step M4.5 of docs/m4/M4_PLAN.md ("Skills library on GitHub, load when needed") on prometheus-v3. Read CLAUDE.md, the plan, docs/m4/MANIFEST.md and prometheus/skillbook.py. Ask me before creating any GitHub repository (it will be <me>/prometheus-skills, made only after my yes). Prometheus keeps only index.json, downloads a skill when a task needs it, checks every file's sha256 against the index, caches it, drops skills unused for 30 days, works offline from the cache, and publishes an accepted skill as a pull request after my yes. Tests first (tests/test_m4_library.py, GitHub calls faked), Windows first. Run the full suite, push, check GitHub's Windows run, report.
```

### M4.6 — Sonnet
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.6) and follow it step by step; stop and ask me if anything in it is unclear. Build step M4.6 of docs/m4/M4_PLAN.md ("Sandbox test-run before switching on") on prometheus-v3. Read CLAUDE.md, the plan, docs/m4/MANIFEST.md, prometheus/sandbox.py and prometheus/own_tools.py. Every script of a new or changed skill runs once in the sandbox with the manifest's sample inputs; a failure, timeout or unpermitted network use keeps the skill off and shows the reason in Abilities → Candidates. Tests first (tests/test_m4_trial.py). Don't change sandbox.py's rules. Run the full suite, push, check GitHub's Windows run, report in plain words.
```

### M4.7 — Sonnet
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.7) and follow it step by step; stop and ask me if anything in it is unclear. Build step M4.7 of docs/m4/M4_PLAN.md ("Repo knowledge for search") on prometheus-v3. Read CLAUDE.md, the plan, prometheus/file_index.py and prometheus/repos/profile.py. Put useful parts of accepted repos (README sections, docstrings, examples) into the existing file index as searchable notes marked with their source repo and commit, as untrusted text. Tests first (tests/test_m4_knowledge.py). Run the full suite, push, check GitHub's Windows run, report.
```

### M4.8 — Sonnet
```
First read docs/m4/M4_STEPS.md ("Rules for every step", the shared shapes, and section M4.8) and follow it step by step; stop and ask me if anything in it is unclear. Build step M4.8 of docs/m4/M4_PLAN.md ("A notes file per project") on prometheus-v3. Read CLAUDE.md, the plan, prometheus/projects.py and M1's atomic-write helpers. Each project and each of my own repos gets NOTES.md (what it is, decisions, changes), written atomically with a lock so two agents can't overwrite each other, and shown on the project page. Tests first (tests/test_m4_notes.py, including two writers at once). Run the full suite, push, check GitHub's Windows run, report.
```

### Review after each pair of steps — Opus
```
First read docs/m4/M4_STEPS.md ("Reviews") and follow it. Review steps <M4.x and M4.y> of docs/m4/M4_PLAN.md on the branch prometheus-v3, as an independent reviewer who did not build them. Read CLAUDE.md, the plan and the diff since the commit before those steps. Check security first (untrusted repo text, nothing run outside the sandbox, hashes checked, permissions least-privilege), then Windows behaviour, then correctness with real inputs. Fix only what you can prove with a test that fails first; list the rest. Report findings ranked by severity, in plain words.
```
