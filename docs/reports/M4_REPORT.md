# Prometheus 3.0 — M4 report (repositories become skills)

Final audit, 21 November 2026, branch `prometheus-v3`. **Not merged into main** (see "Merge" at the end).

## What M4 does, in plain words
You point Prometheus at a GitHub repository ("add repo 3" after Repos of the day). After your yes on Approvals it
downloads one fixed version of it as a zip (no `git`, so nothing in it runs), refuses unsafe file names and anything
too big, checks it for malware, and writes a **profile**: licence, languages, size, main files, dependencies and the
README (kept as untrusted text). It maps the code (functions, classes, imports) and suggests a **skill** on its card in
**Abilities → Candidates**: a short guide (`SKILL.md`) plus, when the licence allows it, up to three small scripts
taken from the repo, with a `manifest.json` that fingerprints every file (sha256) and lists only the permissions the
scripts need. Share-alike (GPL-style) licences give a guide only until you press **Allow copying**; no licence gives a
guide only, always.

**Accept** saves the skill switched off and test-runs every script once in the locked-down sandbox (no internet, only
its own scratch folder). It switches on only if every script ran and passed. Only a skill that passed its trial, on
exactly those files, can be **published** to your private skills library on GitHub, as a pull request you merge.
Other devices keep only the library's index (a few KB) and download a skill the first time it is needed, checking
every file's sha256; unused skills are dropped after 30 days. Accepted repos also add searchable notes (README
sections, docstrings, examples), marked with their source and kept as untrusted text. Each project gets a `NOTES.md`
(what it is, decisions, changes).

## Exit gate (docs/m4/M4_PLAN.md), run for real in this audit
Real repos, read from their own zips through a fake GitHub (this cloud session can't reach GitHub's API for other
repositories); the library was a fake GitHub in memory, not your real `Reevias/prometheus-skills` (publishing there
opens real pull requests in your account, which wasn't asked for).

| Repo | Result |
|---|---|
| **TheAlgorithms/Python** (Python, MIT) | Skill with 3 scripts (proof_of_work, shuffled_shift_cipher, binary_search_tree_recursive); all 3 **passed** the sandbox trial; permissions `none`; published; loaded on a second "device" from the index, every hash checked; a changed file was refused |
| **lukeed/clsx** (JavaScript, MIT) | **Guide only** (the builder writes Python scripts only); validated, trial passed (nothing to run), published, loaded |
| **Reevias/Prometheus-** (yours, main at 9a24997) | **Failed**: repo_check calls it dangerous and deletes it (the README's install line `irm … \| iex`, and repo_check.py's own malware patterns) |
| Also tried in the reviews | pallets/itsdangerous (guide; now kept as "check this first" thanks to your 21 Nov rule), mahmoud/boltons (its one script loops forever: the trial stopped it, the skill stayed off), python-humanize/humanize and keon/algorithms (guides) |

**Gate not met**: no skill from one of your own repos, and the real library wasn't used.

## Findings of this audit
**Important (open)**
1. **Your own repos aren't treated as yours** (`prometheus/repos/skillmaker.py:553`, `read_repo(...)` is never given
   `own=True`): the profile's `own` is always false, so the map of how your repos link (`codemap.link_repos`) is never
   made and the planned `NOTES.md` for your own repos doesn't exist (only projects have one). This half of M4 is not
   wired.
2. **Your main repository can't become a skill**: repo_check deletes it as dangerous (finding above). Needs your
   decision, for example: a repo you own gets "check this first" instead of "danger" for pattern matches (Defender
   still deletes real threats), or Markdown files are only shown, never judged.

**Minor (open, from Review B, still true)**: Accept waits for the whole trial with nothing on screen (73 s with the
boltons script); the trial hashes a script, then reads it again to run it (only something already on the laptop could
swap it); a skill whose manifest.json is removed switches on without a trial; nothing
runs skill scripts for real yet, so `still_valid` must be checked by whatever does that first (M5/M6); the drafted
TheAlgorithms skill lists `pytest` as a package (it stays off on a laptop without pytest).

**Reviews A and B**: all Critical/Important findings are fixed, each with a test that failed first (bug log 150–157):
reader (unsaveable files refused, folders counted, a repo can't plant `profile.json`/`codemap.json`), Candidates
(unsafe or unchecked repos can't be accepted), library (publishing needs a passed trial), Accept (skipped internet
scripts keep the skill off), repo_check (your `.devcontainer/`/`.github/` rule).

**New code that arrived during this audit** (b93a121, M4.4 extra): a **Publish to library** button on a skill's
page (only for your own switched-on skill whose trial passed on exactly these files; it asks on Approvals first, and
the approval re-checks the trial), and repo_check's setup-folder rule now counts only files *inside* those folders.
Reviewed: no new findings. The numbers below are for the code with it merged in.

## Numbers, and where they ran
| Where | What | Result |
|---|---|---|
| Here (Linux cloud, Python 3.14.6) | full suite on `5c16046` (before b93a121) | 1648 passed, 64 skipped, 0 failed |
| Here | browser click-through on `5c16046` | 24 passed |
| Here | full suite on the merged code (with b93a121) | 1664 passed, 64 skipped, 0 failed |
| Here | click-through on the merged code | 24 passed |
| Here | M4's own tests: reader 21, code maps 15, candidates 8, skill builder 35+, library 15+, trial 13, knowledge 7, notes 7 | 121 before b93a121 (it adds more), part of the suite |
| GitHub (Windows and Ubuntu, `5c16046`) | Ubuntu 3.14 and 3.15, Windows 3.14 and 3.15, Windows click-through, Windows add-ons | all 6 green |
| GitHub, this report's commit | the same 6 jobs | see the commit after this one (the report cannot contain its own result) |
| Your laptop | M4 | **not run yet** |

## NOT VERIFIED ON WINDOWS (on your laptop)
- The real GitHub download, Defender's scan of a downloaded repo, and deleting a dangerous folder while Defender holds
  a file.
- Paths longer than 260 characters being refused cleanly (tested by the same kind of failure on Linux and GitHub's
  Windows machine only).
- A trial run in the sandbox and a library download on your laptop; publishing to your real `prometheus-skills`.
- The Candidates page and the project NOTES.md on your screen.

## Residual risks
- No scanner catches everything: "Looks clean" means nothing known-bad was found.
- Script checks are read-only guesses (Python's `ast`); the real protection is the sandbox, which wasn't changed.
- Library skills are trusted because they passed a trial before publishing and their hashes are pinned; anyone who
  can merge into your library repository can add skills, so keep it private and merge only Prometheus's pull requests.
- The library key needs Contents and Pull requests (read and write) on `prometheus-skills` only.
- Code maps miss CommonJS `require(...)` imports and `.d.ts` functions; JavaScript repos give guides only.

## Merge
Not merged. Conditions not met: (1) two open Important findings (above); (2) the owner's laptop check of M4 hasn't
been done and "merge M4" wasn't written in this session. GitHub's run on the branch is green.
