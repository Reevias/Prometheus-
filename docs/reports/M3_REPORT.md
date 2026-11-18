# M3 report: orchestration and model routing (16 November 2026)

M3 is on the `prometheus-v3` branch. The original six steps were reviewed on 16 November. The Phase A and C changes were tested in a local Windows virtual environment. The owner's independent review of those changes is pending.

## What it does now
| Step | What you get |
|---|---|
| M3.4 Model cards | Every AI has a card: exact name, what it's good at (quick talk, planning, research, code, pictures, summaries, your files), strength and price. Each job goes to a fitting AI. Heavy jobs (planning, research, code): the best fit, and a cheap paid AI (at most $1/$4 per million tokens) may take it without asking, up to $0.10 a task, inside your monthly cap. Light jobs: free AIs first. Your own picks in Settings always come first. Your files only ever go to the laptop's AI; a picture-making AI never gets anything else. Settings → AI → Advanced lists the cards, with "Check cards". |
| M3.2 Tool list | Every tool says what it needs and where it runs (laptop, phone or both). The planner sees it. A phone job asked while the phone is away waits up to a day (at most 3 tries) and runs when the phone is back; you're told both times. |
| M3.1 Front door | Greetings and general-knowledge questions get a quick direct answer. Anything that needs a tool or fresh facts gets the normal plan. Long jobs with several parts get the step-by-step loop. If something needed is missing and guessing would change what happens, it asks one question. Clear cases are decided by rules (instant); unclear ones by a free AI in 4 seconds, else the normal plan. |
| M3.3 Step by step | Think, act, look: one action per round, up to 12 rounds, each one visible on the task with a short "why". Approvals, Stop, Pause, spending limits and privacy apply every round. Not used on the cloud copy (long laptop jobs wait for the laptop there). |
| M3.5 Streaming | Answers appear word by word (OpenRouter, Coarena, the free providers and the laptop's Ollama). Claude also streams answer text through its SDK. |
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
The table records the results available on 17 November. The local Phase A and C runs are recorded below.

| Where | Result | When |
|---|---|---|
| This cloud session (Linux, Python 3.14.6): the full suite | **1365 passed, 41 skipped** | 17 Nov, after audit Phase B and D1-D5 |
| Browser (click-through) tests | not re-run in that session; earlier runs are not counted here | NOT VERIFIED since Phase B |
| GitHub `windows-latest` (not your laptop) | see the Actions tab for each push | the owner's laptop: **not yet** |

Earlier versions of this report said "1353 tests pass, plus 4 browser tests". That number came from the build session
and is replaced by the table above. New M3 tests (written with the features): cards, tool list, front door, loop,
streaming, live context and the review fixes, in `tests/test_v3_m3_*.py`; the audit fixes are in
`tests/test_v3_m3_audit_b.py`.

## Phase A and C review, 19 November 2026

The Windows virtual environment used Python 3.14.10. The Phase A full run passed 1,414 tests, skipped 49 and reported two warnings. Four skips came from the missing Playwright browser. The targeted run of `tests/test_v3_m3_audit_a.py` and `tests/test_v3_m3_audit_c.py` passed all 52 tests. The SDK streaming test uses an offline HTTP response with separate thinking and text events.

The combined Windows run of `python -m pytest -q --tb=short` passed 1,426 tests, skipped 41 and reported three warnings. Chromium ran all five browser tests, including the phone and desktop activity label. Two warnings came from a deprecated Hugging Face download option and one from a Windows asyncio pipe during cleanup.

After correcting timing and control identification in the browser test, `python -m pytest -q tests/test_v3_clickthrough.py --tb=short` passed all five checks independently, with one dependency warning.

The activity field at `/api/v3/state` describes the connected instance. The desktop and mobile web layouts read the same field. Stop all takes priority. When idle, the interface hides the activity label. The field contains fixed labels and round numbers without the contents of requests, answers or approvals.

## Not done / known limits
- Claude streaming was verified with test responses. A live paid call and a physical Android phone remain unverified.
- (Fixed in audit Phase B1, 17 Nov: Pause in the step-by-step loop now keeps its place and Carry on continues from that round.)
- Strength levels on the cards are Prometheus's own estimate, not a benchmark. Edit your own copy to change them.
- "Cheap paid AI for heavy jobs" is **on** after updating (your choice). Turn it off in Features if you'd rather be
  asked every time.
