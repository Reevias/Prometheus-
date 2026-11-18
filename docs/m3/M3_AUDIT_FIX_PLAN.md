# M3 fix plan (from the combined audit of 17 November 2026)

Source: `Prometheus_M3_Combined_Audit_v2.pdf` (two audits + the "Ambient Intelligence" proposal). The owner asked for a
plan that can be built without further questions, so every open choice below is decided **the safest way** and marked
*(decided)*. Each fix follows the usual rule: **a test that fails first, the fix, the test passes**, then the full suite,
the browser tests and GitHub's Windows run. Work stays on `prometheus-v3`; nothing goes to `main` without the owner.

Checked in the code before writing this (17 Nov): sandbox `ActiveProcessLimit = 1` with `sys.executable`
(sandbox.py:212, 247); `free_no_key = True` and `auto_update = True` by default (config.py:148, 85); `Approvals.resolve`
has no Stop-all check (safety/approvals.py:51); `update_self` runs without an approval (agents/github.py:51); seven
case-sensitive `"<untrusted"` checks; fpdf2, pywebpush and pystray in pyproject.toml. All audit claims held.

## Who builds what (owner, 17 Nov)
- **Phases A and C:** reassigned by the owner on 18–19 November and completed for `prometheus-v3`. The owner will arrange an independent review.
- **Phases B and D:** another Claude session, given by the owner.
- Both work on `prometheus-v3`: `git pull` before starting and before every push, adopt the other's work, never
  overwrite it, never force-push. Phase D's exit gate (D6) runs after A, B and C are all in.

## Phase A: blockers (before the branch is used on the laptop)

Phase A was implemented on 18 November. The local Windows regression tests are in `tests/test_v3_m3_audit_a.py`. See `docs/reports/M3_REPORT.md` for verification results.

| # | Audit item | Fix | Test (fails first) |
|---|---|---|---|
| A1 | **Sandbox fails every script in a venv on Windows** (Defect 1, B7): `.venv\Scripts\python.exe` is a launcher that starts a second process, which `ActiveProcessLimit = 1` forbids (ERROR_NOT_ENOUGH_QUOTA). GitHub CI didn't catch it because CI doesn't use a venv. | Start the guard with the real interpreter (`sys._base_executable`) and pass the venv's `site-packages` folders to the guard, which adds them to `sys.path` itself (so `-I` stays on and the limit stays 1). Make the ctypes test expect the real Windows behaviour. | A Windows CI job that runs the sandbox tests **from a venv** (new step in tests.yml); unit test that the command uses the base interpreter and passes site-packages. |
| A2 | **Front door answers money/study/data questions with no tool** (B1, B11): "what did I buy last month", "how much did I spend on food", "how many hours did I study", "how much data is left". | Owner-data words (spend, spent, buy, bought, paid, cost, budget, money, expense, study, studied, hours, revision, data left, my plan, balance, bill, salary, marks, grade, attendance, exam, deadline, due) never route to "simple". Plus a hard rule: anything with "my/I/me" + a number/amount question goes to the plan. | Parametrised cases from both audits plus 20 more owner-data phrasings, in test_v3_m3_review. |
| A3 | **`<UNTRUSTED>` in capitals slips past 7 checks** (Risk 1). | One helper `has_untrusted(text)` (case-blind, also catches `&lt;untrusted`), used everywhere. | Each of the 7 call sites with `<UNTRUSTED>`, `<Untrusted >`. |
| A4 | **Keyed "free" providers are always $0 and never asked** (B2). | Catalog providers stay free *only while on their free tier*: each gets a Settings switch "I turned on billing at this provider" *(decided: default off, as they are free-tier keys)*; switched on, it counts as paid (needs a yes, counted at its card price or tokens). Every call's tokens are recorded either way, so use is visible on Costs. | A keyed provider marked billing-on asks for a yes and records a cost; one left on free tier records tokens. |
| A5 | **"Update yourself" with no approval; auto-update pulls `main` on this branch; `pip install -e .` runs new code** (B3, Risk 3, open item 3). | (a) Chat "update yourself" asks first, showing the incoming commits. (b) Auto-update pulls **the branch it is on** (not always `main`). (c) If an update changes dependencies (pyproject.toml) or adds new top-level code paths, it **asks** instead of installing silently. (d) Auto-update stays on *(decided: the owner chose it on 12 Nov)*, but only fast-forwards from `origin`, never runs any repo script. Signed-tag checks need a key the owner doesn't have yet: recorded in ideas for M7. | Update via chat creates an approval; auto-update on a branch pulls that branch; a dependency change waits for a yes. |
| A6 | **Stop all doesn't stop approvals** (B4, open item 11). | `Approvals.resolve` refuses (and the routes say so) while everything is stopped; nothing queued runs until Resume. | Approve while paused gives "Everything is stopped" and the handler doesn't run. |

## Phase B: medium items (DONE 17 Nov: B1 to B8, tests in `tests/test_v3_m3_audit_b.py`; full suite here 1359 passed, 41 skipped; GitHub Windows run not yet checked)

| # | Audit item | Fix | Test |
|---|---|---|---|
| B1 | **Loop marks a task "done" while an approval waits; Pause promises a carry-on it doesn't keep** (Risk 2, B9). | Save the loop's state (messages, round, results) like a paused plan; the task status is "waiting"/"paused"; after the yes, or Carry on, the loop continues from that round. | Approve mid-loop: the loop continues from the next round; Pause then Carry on: same. |
| B2 | **Keyless providers on by default, and they get memories** (B5). | Default `free_no_key` off for new installs *(decided)*; and keyless providers only ever get the request and recent turns, never memories, facts or project notes. | A keyless provider's call contains no memory or fact text. |
| B3 | **Webhook token in the URL and in the access log** (B6, open item 9). | Accept the token in a header too; filter `/hooks/<token>` out of uvicorn's access log everywhere. | The log line shows `/hooks/***`. |
| B4 | **Secret scanning misses MCP, webhook and sync tokens** (open item 7). | Scan/redact every saved secret (all of `.env`, MCP_* and the webhook/sync tokens). | Terminal output containing each token is redacted. |
| B5 | **Moving/copying an existing .bat/.ps1 skips the runnable rule** (open item 1). | Copy/move of runnable files asks first. | A move of a .ps1 waits for a yes. |
| B6 | **Recycle >10 files can be split into steps of 10** (open item 2). | Count recycled files per task, not per step. | 3 steps of 8 files: the third asks. |
| B7 | **Antigravity spending checked only at start** (open item 4). | Reserve each job's estimated cost when it starts; release the rest at the end. | Two jobs can't both pass a cap meant for one. |
| B8 | **Cloud copy can queue laptop jobs that change things** (open item 13). | A job from the cloud that would change something waits for a one-tap yes (phone notification or laptop). Read-only jobs run as now. | A read_only=0 job creates an approval instead of running. |

## Phase C: what the owner wants added to M3

Implemented 19 November. The phone uses the same responsive interface as the laptop. The activity endpoint reports the state of the Prometheus instance the device is connected to.

| # | Addition | What gets built |
|---|---|---|
| C1 | **Claude streaming** (named weakness). | `AnthropicProvider.stream` using the SDK's streaming with the same adaptive thinking and fallback; text deltas only. |
| C2 | **Ambient Intelligence, the M3 part** ("intelligence foundation": understand what is going on and pick the right response; make the assistant's state legible). | An **assistant state** the interface can show: listening, thinking, waiting for your yes, acting, answering, stopped. Exposed in `/api/v3/state` and on the orb/phone, from real events only. The front door and loop report which step they're on. *(decided)* The rest of the proposal stays in its milestones: voice engine and full-duplex talk (M8), security checks like "my password may be exposed" (M7, only with explicit permission and an authorised service), proactive watching (M10). Written into docs/ideas-after-m10.md and PLAN.md's mapping, not built now. |

## Phase D: truth in the documents, licences, and the gate (D1–D5 implemented; D6 requires an independent review and verified test runs)

| # | Item | Fix |
|---|---|---|
| D1 | M3 report says "1353 tests pass" (claim list). | Report only numbers produced by a run, with where it ran (here / GitHub Windows / laptop not yet). |
| D2 | PLAN.md claims `browser_action` with an accessibility tree (B10). | Correct it: the browser agent reads labels and aria; an accessibility-tree tool moves to M5. |
| D3 | github.py:67 tells the planner updates wait for approval; CHANGELOG says updates need no approval. | Make both true after A5 and reword them. |
| D4 | models.json "prices checked 11 Nov" (prices drift). | Say prices are a snapshot; "Check cards" compares them with the live OpenRouter list and shows the difference. |
| D5 | Copyleft dependencies with no sign-off (B8): fpdf2 (LGPL-3.0), pywebpush (MPL-2.0), pystray (LGPLv3). | *(decided)* Replace **fpdf2** with a permissive PDF writer (ReportLab, BSD) if the PDF tests pass with it; keep pywebpush and pystray (used unmodified as separate libraries) and list them in `THIRD_PARTY_LICENSES.md` with their obligations and a sign-off line for the owner. |
| D6 | Exit gate. | Full suite green here **and** in a Windows venv job; browser tests; an independent review of the fixes; report with honest numbers; CHANGELOG, bug log, PLAN updated. |

## Order
A1 → A6 (blockers), then B1 → B8, then C1 → C2, then D1 → D6. A commit per item; push after each phase; check GitHub's
Windows run after each push.
