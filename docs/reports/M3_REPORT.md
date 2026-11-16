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
1353 tests pass, plus 4 browser tests. New M3 tests: cards 10, tool list 5, front door 14, loop 6, streaming 6, live
context 3, review fixes 30.

## Not done / known limits
- Claude doesn't stream yet (its official library with adaptive thinking; a separate change).
- Pause in the step-by-step loop stops it; "carry on" starts it again from the request (a plan's Carry on is exact).
- Strength levels on the cards are Prometheus's own estimate, not a benchmark. Edit your own copy to change them.
- "Cheap paid AI for heavy jobs" is **on** after updating (your choice). Turn it off in Features if you'd rather be
  asked every time.
