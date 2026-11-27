# M6 audit (27 November 2026)

Branch `prometheus-v3`. **Not fully independent:** this audit was run by the Opus session that built 6.1 and 6.2. To
make up for that, 6.1/6.2 got a second look from a separate reviewer agent that hadn't seen the builder's reasoning
(all its findings checked and fixed or written down below); 6.3–6.5 (built by Sonnet and Antigravity, already reviewed
once: bugs 199–218) were reviewed by this session. `docs/m6/M6_LAPTOP_CHECK.md` named in the audit request doesn't
exist; the laptop and phone facts below come from the plan, `docs/missing.md` and the CHANGELOG.

## Exit gate (docs/PLAN.md, M6)

| Gate item | Result | True numbers / evidence |
|---|---|---|
| Disconnect/reconnect tests | **PASS (tests)** · NOT VERIFIED on Render | Cloudroom: lost link while waiting, answer lost after the server took the task, link down for good, Cloudroom restart after the receipt, laptop restart before and after the receipt, a proxy error page, a 401 (tests/test_v3_m6_cloudroom.py, tests/test_v3_m6_audit.py). Never run against a real Render service. |
| Works offline | **PASS (tests)** · NOT VERIFIED on the laptop | Cloudroom storage queues while offline and sends later; the queue is capped (tests/test_m6_cloudroom_storage.py, tests/test_m6_review_63_65.py). |
| Encryption tests | **PASS** | AES-256-GCM with a per-file key from the recovery code (scrypt), counter nonces, the piece number and "last piece" sealed in; Drive only sees a keyed tag; wrong key can't open; files can't be swapped (6.3 tests). Reviewed by reading the code too. |
| Phone control through mobile-mcp with approvals | **PARTLY** | On the owner's OnePlus Nord 5 (Android 16), 26 Nov: device found, apps listed, Calculator opened, a real screenshot taken, 36 controls read, one tap and Home. The phone locked part way: typing, several taps, password refusal and Settings/Clock reading were **NOT VERIFIED live** (fake mobile-mcp tests only). Every tap/type/app change asks; reading asks once (tests). |
| GitHub Windows CI green | see the CI line in the report reply / CHANGELOG | GitHub's machines, not the owner's laptop. |
| Open-source check of new dependencies | **PASS** | No Python dependency added in M6 (`pyproject.toml` unchanged). New outside programs: `@mobilenext/mobile-mcp` 1.0.20, Apache-2.0, pinned, run as a separate program, in Credits and `docs/m0/DEPENDENCY_MATRIX.md` (checked in 6.0). `cloud/render/requirements.txt` lists only fastapi, uvicorn and psutil, already used by Prometheus. |

## Findings fixed in this audit (bugs 219–232, each with a test that failed first; tests/test_v3_m6_audit.py)
- **High, A1:** a sandboxed task could start a shell through `_posixsubprocess.fork_exec` (it raises no audit event,
  and five routes reach it) and read the Cloudroom token from the server's start-up environment in /proc. Checked as a
  real non-root user here: before the fix the task started a shell; after it the kernel refused (`BlockingIOError`).
  Fix: Cloudroom runs tasks with a process limit of 0 (kernel-enforced; root would ignore it, and the Render image runs
  as a non-root user); the server process is made not dumpable, so /proc/<pid>/environ and its memory can't be read by
  its own user. The laptop (Windows) sandbox already allows only one process through its Job Object.
- **Medium:** a proxy error or a 401 failed a running task as "restarted" (A2); a move cut off by a laptop restart
  stayed "pending" for ever (A3: now paused, with Resume in the jobs view); a watcher that broke left the task
  "running" (A4); the 512 MB server read huge unsigned bodies, kept every task's uploads and read result files whole
  (A5).
- **Low:** CPU time limit fixed at 65 s whatever the task's limit (A6); two watchers after Pause/Resume (A8); a file
  changed between the yes check and the send (A9); the question cut the code at 600 characters (A10); a failed move
  kept its yes (A11); Cloudroom wasn't connected in the app (A12: connected when `cloudroom_url` and the Cloudroom key
  are set).

## Reviewed and judged sound
- 6.4b: random 256-bit device key stored hashed, compared in constant time; pairing code works once, 5 tries, 5 minutes;
  only the private link (Tailscale or the laptop), no proxy headers; dangerous actions also need a yes on the laptop;
  typed text never logged or shown in the pop-up; the browser buttons sit behind login and CSRF.
- 6.3 encryption (above). 6.4a/6.5 approvals as fixed by the earlier review.
- 6.1/6.2 signing, receipts, request-id dedupe, single-use exact yes, other keys refused, event order (second look).

## Left open (honest list)
- Cancelling a task already running on Render marks it cancelled and drops its result, but the program runs to its own
  time limit there (and the next task waits behind it).
- No screen yet to type the Render address (`cloudroom_url`), and nothing in the chat offers to move a task.
- The token in `render.yaml` is an environment variable; a Render secret file would be better (the server reads one if
  present). With A1's fixes a task can neither start another program nor read the server's environment.
- On a Linux laptop (not the owner's) the sandbox guard can still be left through `_posixsubprocess` (threads count as
  processes on Linux, so the process limit is only used on Cloudroom).
- Phone screenshots for "look" default to a cloud vision AI (old behaviour, not new in M6; the router still asks before
  any paid AI).
- One full-suite run here had one failure in an M5 test (`test_status_shows_state_run_time_and_real_memory`, under
  load); 15 repeats and the next full run passed.
- Everything here is NOT VERIFIED on the owner's laptop and on Render; the phone only as listed above.
