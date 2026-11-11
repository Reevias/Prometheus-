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

## Checks before committing
- Tests: `python -m pytest -q` (all must pass).
- Changing `prometheus/prompts/planner.md`: add the old hash to `KNOWN_OLD["planner"]` in `prometheus/selfedit/prompts.py`.
- A new secret: add it to `KEY_NAMES` in `prometheus/config.py`.
- Keep `CHANGELOG.md` and `docs/missing.md` up to date.

## Next steps (agreed 11 Nov 2026), in order
1. **Going public**: scan the whole git history for keys, passwords, personal email or files; remove them (history
   rewrite allowed). Then the owner makes the repo public.
2. **Own icon in Windows search**: the Start-menu entry shows the Python icon. Give the shortcut a stable icon
   path and the app's AppUserModelID, and refresh the icon cache.
3. **One-line install**: `irm <raw link>/scripts/install.ps1 | iex` in PowerShell (works once the repo is public).
4. **Prometheus-Setup.exe**: a real Windows installer, built by GitHub Actions and attached to a GitHub Release.
5. **Android APK**: Prometheus.apk built by GitHub Actions, opening the cloud copy; put on the Release and on the
   /phone page.
6. **Settings: Simple + Advanced**, plus a **Features** list with an on/off switch for every feature.
7. **From the "Build your first AI agent" PDF**: a stop-everything switch, a weekly report card, a practice test
   set, and ready-made jobs (notes into actions and a follow-up, a weekly news digest, PDFs into a spreadsheet with
   gaps marked MISSING).
