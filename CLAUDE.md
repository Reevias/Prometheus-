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
- [ ] 5. Tasks paused by an AI limit resume by themselves when the limit resets
- [ ] 6. Security review of everything added since the second review; glitch sweep of the repo (click-through + agent sweep)
