# M3 report: orchestration and model routing (16 November 2026)

**In short:** all 6 steps of M3 are built, reviewed and tested, on the `prometheus-v3` branch (not on `main` yet: say
the word and it goes there after GitHub's Windows tests pass). **NOT VERIFIED on your laptop**: tested here and on
GitHub's Windows and Linux machines, not on your hardware or with your real AI keys.

## What it does now
| Step | What you get |
|---|---|
| M3.4 Model cards | Every AI has a card: exact name, what it's good at (quick talk, planning, research, code, pictures, summaries, your files), strength and price. Each job goes to a fitting AI. Heavy jobs (planning, research, code): the best fit, and a cheap paid AI (at most $1/$4 per million tokens) may take it without asking, up to $0.10 a task, inside your monthly cap. Light jobs: free AIs first. Your own picks in Settings always come first. Your files only ever go to the laptop's AI; a picture-making AI never gets anything else. Settings → AI → Advanced lists the cards, with "Check cards". |
| M3.2 Tool list | Every tool says what it needs and where it runs (laptop, phone or both). The planner sees it. A phone job asked while the phone is away waits up to a day (at most 3 tries) and runs when the phone is back; you're told both times. |
| M3.1 Front door | Greetings and general-knowledge questions get a quick direct answer. Anything that needs a tool or fresh facts gets the normal plan. Long jobs with several parts get the step-by-step loop. If something needed is missing and guessing would change what happens, it asks one question. Clear cases are decided by rules (instant); unclear ones by a free AI in 4 seconds, else the normal plan. |
| M3.3 Step by step | Think, act, look: one action per round, up to 12 rounds, each one visible on the task with a short "why". Approvals, Stop, Pause, spending limits and privacy apply every round. Not used on the cloud copy (long laptop jobs wait for the laptop there). |
| M3.5 Streaming | Answers appear word by word (OpenRouter, Coarena, the free providers and the laptop's Ollama). Claude still gives the whole answer at the end. |
| M3.6 Live context | The AI is told the time and counts: reminders due today, tasks running, approvals waiting, battery. Never titles. |

## Your choices (16 Nov)
8 job kinds; best fit with cheap paid AI for heavy jobs ($0.10 a task, monthly cap, Features switch, **on**);
cards in Settings → AI → Advanced; 12 rounds; live context counts only; ask only when guessing would change things;
a job for an absent device waits and you're told.

## Bugs
An independent review of M3 found 13 problems; all are fixed, each with a test that failed first (bug log 96-108).
The two worst: the front door answered questions that need a tool ("any new emails?", "who won the match?") without
the tool; and a crash in the step-by-step loop left a task "running" for ever. Also fixed: the Windows-only test
failure since the security audit (a test assumed `ctypes` can be imported in the Windows sandbox; it can't, which is
stricter).

## Tests
Only numbers that a run produced, and where it ran (corrected 17 Nov by the M3 audit fix plan, D1):

| Where | Result | When |
|---|---|---|
| This cloud session (Linux, Python 3.14.6): the full suite | **1361 passed, 41 skipped** | 17 Nov, after audit Phase B |
| Browser (click-through) tests | not re-run in that session; earlier runs are not counted here | NOT VERIFIED since Phase B |
| GitHub `windows-latest` (not your laptop) | see the Actions tab for each push | the owner's laptop: **not yet** |

Earlier versions of this report said "1353 tests pass, plus 4 browser tests". That number came from the build session
and is replaced by the table above. New M3 tests (written with the features): cards, tool list, front door, loop,
streaming, live context and the review fixes, in `tests/test_v3_m3_*.py`; the audit fixes are in
`tests/test_v3_m3_audit_b.py`.

## Not done / known limits
- Claude doesn't stream yet (its official library with adaptive thinking; a separate change).
- (Fixed in audit Phase B1, 17 Nov: Pause in the step-by-step loop now keeps its place and Carry on continues from that round.)
- Strength levels on the cards are Prometheus's own estimate, not a benchmark. Edit your own copy to change them.
- "Cheap paid AI for heavy jobs" is **on** after updating (your choice). Turn it off in Features if you'd rather be
  asked every time.
