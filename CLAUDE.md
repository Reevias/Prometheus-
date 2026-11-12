# Working on Prometheus

Prometheus is the owner's personal AI assistant for their Windows laptop (8 GB RAM, no GPU), with a cloud copy and a
phone app. The owner is a student: use plain words.

## How the owner wants to work
- Ask lots of questions before building anything (2 to 4 at a time, recommended option first). Ask again whenever
  something is unclear, rather than guessing.
- One step at a time, short reports. Don't repeat the "what's missing" list over and over.
- Don't keep adding options to Settings: it is getting cluttered. New things go in the simple Features list (one
  on/off switch each), and technical knobs go under Advanced.
- Only raise the version number for a real release.
- AI models: don't add expensive ones (over $1 in or $4 out per million tokens). Paid models are only used after the owner says yes; free ones first.

## Checks before committing
- Tests: `/home/user/venv314/bin/python -m pytest -q` (Python 3.14, the version Prometheus needs; all must pass).
- Changing `prometheus/prompts/planner.md`: add the old hash to `KNOWN_OLD["planner"]` in `prometheus/selfedit/prompts.py`.
- A new secret: add it to `KEY_NAMES` in `prometheus/config.py`.
- Keep `CHANGELOG.md`, `docs/missing.md` and `docs/bugs.md` (one line per bug fixed, update the total) up to date.

## Status (11 Nov 2026)
Done: history checked (no secrets; only the first commit's author email, kept because rewriting would break
`git pull --ff-only` updates on the laptop and the cloud copy), icon and app id on the shortcut, one-line install,
`installer/prometheus.iss` and `.github/workflows/release.yml` (Setup.exe + APK on a Release), `android/` app,
Settings Simple + Advanced with `prometheus/features.py`, persistent Stop all, `report.py`, `practice.py`,
`agents/jobs_agent.py`.

Waiting on the owner:
- Make the repository public. Every GitHub Actions run fails within seconds while it's private (no runner starts:
  the free minutes are used up), so the Release can't be built until then. After that, run "Release" from the
  Actions tab (or push a tag `v2.3.0`).
- Optional: add `ANDROID_KEYSTORE` (base64 .jks) and `ANDROID_KEYSTORE_PASSWORD` secrets, so new APK versions install
  over old ones. Without them each APK is signed with a throwaway test key.

Not yet checked on real hardware: the shortcut app-id PowerShell (the release workflow checks it on Windows),
the APK, the clap and ElevenLabs voice.

## In progress (12 Nov 2026): work through in order, tick off as done
Owner's answers: resume on limits = both (Prometheus resumes paused tasks itself; Claude leaves notes here);
repo of the day = list top 10, owner picks which to add; translate = repo descriptions, "translate this",
files and web pages, what's on screen; memory = "remember…/update memory…" always saves instantly, no AI.
- [x] 1. Microphone: "PortAudioError … MME error 1" → try other ways to open the mic (WASAPI, native rate + resample), plain message
- [x] 2. Instant memory: remember / note / update memory / don't forget / forget / change X to Y, without AI
- [x] 3. Repo of the day: top 10 list, English descriptions, pick which to add
- [x] 4. Translate (repo descriptions wired in step 3): text, files and web pages, screen, repo descriptions
- [x] 5. Tasks paused by an AI limit resume by themselves when the limit resets
- [x] 6. Security review of everything added since the second review; glitch sweep of the repo (click-through + agent sweep)

## Project direction: Prometheus 3.0 (owner's roadmap PDF, 12 Nov 2026)
Development branch: `prometheus-v3` (branched from main at 8fe8ae2; holds M1 + M2 work). Main is the current 2.x line.
**M0 and M1 are merged into main (12 Nov 2026)**, copied commit by commit from the branch (files identical). M2 stays on
the branch until it is audited.
Rules: preserve the Python/FastAPI/Jinja2/HTMX/Canvas/PyWebView stack; evidence over claims (code + tests + real
runs + report); a milestone starts only after the previous one is accepted and audited; never fabricate telemetry,
progress or results; enforce security in code, not prompts; logical commits on the branch, no force-push or
unapproved merges to main; document residual risks honestly; destructive actions need explicit approval.
Also follow `AGENTS.md` on the v3 branch (colours #0B0B10 #17171F #211D1A #FF922B #FFC06A #F4F1ED #AAA5A0; home stays
minimal; LocalRuntime default, Cloudroom optional).

| Milestone | Purpose | Status (per register) |
|---|---|---|
| M0 | Baseline, test isolation | Done, merged into main |
| M1 | Tool primitives: path hardening, atomic FS, bounded reader/diff, async terminal, services integration | Done, merged into main: 5 findings + 16 from a second independent audit fixed; tests pass on Windows and Linux (docs/reports/PROMETHEUS_M1_FINDINGS_FIX_REPORT.md) |
| M2 | Desktop UI from the owner's orange-orbital screenshot (sidebar: Home, Agents, Skills, Tasks, Projects, Memory, Automations, Research, Integrations, System; orbital core; command bar; slash palette; right panel with real health/CPU/RAM/GPU; bottom wave; native window controls) | Spec exists; commits on branch (31a4aac…34793a3) but not audited; audit only after M1 approval |
| M3 | ReAct orchestrator and model routing | Planned |
| M4 | Repository-to-skill intelligence (separate repo hub for candidates) | Planned |
| M5 | DAG orchestration, concurrent child agents | Planned |
| M6 | ExecutionRuntime / Cloudroom | Planned |
| M7 | Security hardening, safe-stop system | Planned |
| M8 | Multi-layer memory/context engine | Planned |
| M9 | End-to-end evaluation, release audit | Planned |
| M10 | Jarvis-style autonomy (reference hectorg2211/jarvis); reserved: don't pull into M0–M9 | Reserved |

Merge plan (agreed approach): merge into main one milestone at a time, after that milestone passes its tests, a real
Windows run and an audit. Don't wait for M0–M10. M1 and M2 share the branch, so M1 is merged first on its own.
Next: audit M2 (on the branch). M1's original findings, all fixed:
1. Caller environment overrides can bypass secret filtering (terminal).
2. Thread-resumption failures need fail-safe handling (suspended process + Job Object).
3. `read_window` still loads the whole file into memory.
4. Journal-derived recovery paths and backup integrity need validation.
5. Pathname replacement keeps a documented TOCTOU window.
