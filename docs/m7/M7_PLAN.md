# M7 plan: Security hardening and safe-stop (27 Nov 2026)

Status: **plan written 27 Nov 2026.** M7 starts after M6's audit is accepted by the owner (PLAN.md rule 3).
Steps 7.1 to 7.5 are M7 in `docs/PLAN.md`; 7.6 and 7.7 are the two items already parked for M7 in
`docs/ideas-after-m10.md` (remember-me login, leak check). Nothing else is added. Work happens on `prometheus-v3`.

## Owner's answers (27 Nov 2026)
- **New project's tools:** same rules as today (safe tools allowed, risky ones ask); the owner can turn tools off or
  make them ask, per project.
- **Keys:** move from the plain `.env` file into Windows Credential Manager, check each one reads back, then wipe the
  `.env` copy. The cloud copy (Linux / Render) keeps using its own environment settings.
- **Parked items:** both in: remember-me login and "has my password leaked?".
- **Audit log and undo list:** kept 90 days, then cleared.

## What M7 gives the owner
Prometheus can't be tricked into doing something the owner didn't approve, and this is proved by tests that try.
One STOP ALL really stops everything: agents, jobs, the laptop and Cloudroom runtimes, PC and phone control. Every
action is written to a record that can't be quietly changed, and most can be undone. Keys are in Windows' own safe
store, not a plain file. Each project has its own tool rules.

## Steps

| # | Step | What "done" means | Tests that must fail first |
|---|---|---|---|
| 7.0 | **Map of every action** | A list of every way Prometheus can act (each tool, agent, route, runtime, phone and PC action, automation) with: who can start it, whether it asks, how it is stopped, how it is undone. Written in `docs/security/ACTION_MAP.md`. | — |
| 7.1 | **Per-project permissions** | Each project has a tool list (allowed / asks / off), starting from today's rules. Enforced in code where tools are called, not in the prompt. Covers M3 tool scopes, M5 own agents, M6 runtimes. | A tool turned off for project A is refused there and still works in project B; a model asking for it gets "not allowed". |
| 7.2 | **Keys in Windows Credential Manager** | On Windows every key is stored in Credential Manager (Windows' own API through `ctypes`, no new package). Moved from `.env`, read back, then the `.env` copy wiped. Linux and the cloud copy keep environment settings. | After moving, `.env` holds no keys; every key still works; a failed move leaves the key in `.env` and says so. |
| 7.3 | **Safe-stop, audit log, undo** | STOP ALL stops every agent, job, graph, runtime (laptop and Cloudroom), PC control, phone control, voice, automations and watchers; nothing new starts and no approval can be given until Resume. A record of every action (append-only, each entry linked to the one before so a change shows), kept 90 days. One undo list for everything that can be undone. | Stop mid-action for each kind → stopped within 2 seconds, no child programs left; an edited record entry is detected; undo works for each undoable kind. |
| 7.4 | **Threat model** | `docs/security/THREAT_MODEL.md` rewritten for M1–M6: each threat → the control in code → the test that proves it → leftover risk. Includes signed updates (still no key: written as a risk), Linux process clean-up from M1, and the Render and phone links. | — |
| 7.5 | **Attack tests** | A test suite that tries to trick Prometheus: orders hidden in web pages, repos, files, emails, PDFs, the phone and PC screen, tool output; fake approvals; asking for keys; path tricks; remote control without the device key; Cloudroom without the token. | **Exit gate: zero unapproved actions across the whole suite.** |
| 7.6 | **Remember-me login** | "Remember this device" on login: a random device token (stored only as a hash), 30 days, shown in a device list where each can be removed. Changing the password removes them all. | Stolen cookie from another device fails after removal; expired token fails; password change logs every device out. |
| 7.7 | **Has my password leaked?** | On the owner's request only: checks a password against Have I Been Pwned's free Pwned Passwords service. Only the first 5 characters of its SHA-1 fingerprint leave the laptop; the password never does. | The request sent holds only 5 characters; nothing runs without the owner asking. |
| 7.8 | **Audit and report** | Fresh-session audit, fixes, `docs/reports/M7_REPORT.md`, CHANGELOG, missing.md, bugs.md. | — |

**Exit gate** (from PLAN.md): no unapproved action in the test suite; safe-stop tests pass; GitHub Windows CI green
(not the owner's laptop); laptop check of STOP ALL and Credential Manager; audit passed; open-source check (no new
package planned).

## Work for each step
Each builder does the items in order, a failing test first each time.

### 7.0 Map of every action (Opus)
1. Go through every tool, agent, web route, runtime, automation, watcher, phone and PC action.
2. For each: who can start it (owner, model, automation, phone, cloud), does it ask, how STOP ALL reaches it, can it be
   undone, which project rules apply.
3. Mark every gap (doesn't ask but should, can't be stopped, isn't recorded) as a finding for the step that fixes it.
4. Write `docs/security/ACTION_MAP.md`; list the gaps at the end of this plan under "7.0 findings".

### 7.1 Per-project permissions (builder: Sonnet · enforcement review: Opus)
1. Store per project: each tool allowed / asks / off; a new project copies today's rules (`safety/permissions.py`).
2. One check at the single place where tools are run (the M3 tool runner and the M6 runtime), used by every agent,
   own agent (M5.4), automation and the phone. The project's rule can only make a tool stricter, never looser than
   the global rule.
3. Project page: a simple tool list with three choices each; no new Settings options.
4. A refused tool tells the model plainly "not allowed in this project" and is recorded (7.3).
5. Tests: off in one project, on in another; own agent can't get around it; a stricter rule always wins.

### 7.2 Keys in Windows Credential Manager (builder: Antigravity, on the laptop · review: Opus)
1. `prometheus/safety/keystore.py`: save, read, delete a key with Windows' CredWrite/CredRead/CredDelete through
   `ctypes` (no new package). Entries named `Prometheus/<KEY_NAME>`.
2. `config.get_secret` / `set_secret` use the keystore on Windows; on Linux and the cloud copy, the environment as now.
3. One-time move: each key in `.env` is saved, read back and compared; only then removed from `.env`. If any fails,
   it stays in `.env` and the owner is told which one.
4. After the move, `.env` is overwritten without keys (not just deleted), and the old backup copies made by M6 storage
   still contain only encrypted keys.
5. The secret scanner (`safety/secrets.py`) still knows every saved key so it can block them going out.
6. Tested on the owner's laptop and GitHub's Windows run; a Linux test keeps the environment path working.

### 7.3 Safe-stop, audit log, undo (builder: Opus)
1. STOP ALL reaches everything in the 7.0 map: M5 jobs and graphs, LocalRuntime and CloudroomRuntime (sends stop to
   Render and waits for a receipt), PC control, mobile-mcp, voice, automations, watchers, the resume queue.
2. While stopped: nothing new starts, approvals are refused, phone remote control is refused. Resume lifts the stop;
   paused jobs still wait for their own Resume (M5 rule).
3. Audit log: every action, approval, refusal and stop written append-only to `audit.jsonl`; each line holds the
   fingerprint of the one before, so an edited or removed line is spotted. Files rotate daily; older than 90 days are
   deleted (closes R-5 for `journals/transactions.jsonl` too).
4. A plain-words Activity page reads the log (real entries only).
5. One undo list: file changes (M1 journal), calendar, saved facts, model change, settings, own agents; each says if it
   can be undone; kept 90 days.
6. Tests for every kind of action: stops within 2 seconds, no child programs left (Windows CI and laptop).

### 7.4 Threat model (Opus)
1. Rewrite `docs/security/THREAT_MODEL.md` from the 7.0 map: what we protect (keys, files, accounts, phone, money,
   privacy), who could attack (web pages, repos, emails, other apps, someone on the network, a stolen phone).
2. For each threat: the control in code, the test that proves it, the leftover risk.
3. Include: signed updates (no signing key yet: leftover risk), Linux process clean-up (M1), the Render link and token,
   the phone link and device key, the mobile-mcp server.
4. Hand the attack list to 7.5 as a numbered list in this plan.

### 7.5 Attack tests (builder: Antigravity · attack list and review: Opus)
1. `tests/attacks/`: one test per attack in 7.4's list.
2. Hidden orders in: web pages, repo files and READMEs, PDFs, emails, the phone screen, the PC screen, tool output,
   file names, chat titles.
3. Tricks: fake "the owner said yes" text, asking for keys, `..` and link paths, huge inputs, Unicode look-alikes,
   remote control without the key, Cloudroom without the token, approvals while stopped.
4. Each test checks: no action ran without a real approval, nothing secret left, it was recorded.
5. A found hole stays a failing test marked `pytest.mark.xfail(reason="bug: ...", strict=True)`; Antigravity does not
   fix product code; Opus fixes it and removes the mark.

### 7.6 Remember-me login (builder: Sonnet · review: Opus)
1. "Remember this device" box on the login page (`auth.py`).
2. Random device token in a secure, HTTP-only cookie; only its hash stored; 30 days.
3. Device list (name, last used) on the security page; remove any one; "log out everywhere".
4. Password change removes every remembered device.
5. Tests as in the table.

### 7.7 Has my password leaked? (builder: Antigravity · review: Opus)
1. Only when the owner asks ("has my password leaked?" or a button on the security page).
2. SHA-1 the password on the laptop; send only the first 5 characters to `api.pwnedpasswords.com/range/`; compare the
   rest on the laptop. The password is never saved, logged or sent.
3. Plain answer: "found in N leaks, change it" or "not found".
4. Tests check the outgoing request holds 5 characters only, and that nothing runs without the owner asking.
5. Re-check Have I Been Pwned's terms for this free service and write the date.

### 7.8 Audit and report (Opus, a fresh session · writing: Sonnet)
1. Independent review of all M7 changes and of the 7.0 map against the code.
2. Run the attack suite; every xfail must be fixed or written down as a leftover risk with the owner's OK.
3. Exit gate checks with real numbers; laptop check results.
4. `docs/reports/M7_REPORT.md`, CHANGELOG, `docs/missing.md`, `docs/bugs.md`; anything not run on the laptop is NOT
   VERIFIED.

## Who does what
**Opus decides, builds the safe-stop core and checks; Antigravity does the Windows-native work and the attack tests on
the laptop; Sonnet builds the pages and the login.** Nobody audits their own work.

| Work | Model | Why |
|---|---|---|
| 7.0 map, 7.4 threat model, 7.5 attack list | **Opus** | Security thinking; decides what to test. |
| 7.3 safe-stop, audit log, undo | **Opus** | Touches everything; hardest. |
| 7.2 Credential Manager | **Antigravity** | Windows API; must run on the laptop. |
| 7.5 attack tests | **Antigravity** | Many tests from a clear list; not the builder of the code it attacks. |
| 7.7 leak check | **Antigravity** | Small and self-contained. |
| 7.1 per-project permissions | **Sonnet** builds, **Opus** reviews enforcement | Page + one check point. |
| 7.6 remember-me | **Sonnet** builds, **Opus** reviews | Login page and cookies. |
| Docs, CHANGELOG, report | **Sonnet** | Writing. |
| Final audit | **Opus** (fresh session) | Independent. |

## Order and who works when
| Round | Antigravity (laptop) | Sonnet | Opus | Start when |
|---|---|---|---|---|
| 1 | 7.2 Credential Manager | 7.6 Remember-me | 7.0 map, then 7.4 threat model | M6 accepted |
| 2 | 7.7 Leak check | 7.1 Per-project permissions | 7.3 Safe-stop, audit log, undo | Round 1 pushed |
| 3 | 7.5 Attack tests (from 7.4's list) | Docs | Reviews 7.1, 7.2, 7.6, 7.7 | Round 2 pushed |
| 4 | Laptop check | — | Fixes the attack-test holes (xfails) | Round 3 pushed |
| 5 | — | Report | Fresh-session audit (7.8) | Round 4 done |

Files don't overlap inside a round: Antigravity in `safety/keystore.py`, `config.py`'s key functions, the leak check
and `tests/attacks/`; Sonnet in `auth.py`, project pages and the permission store; Opus in `killswitch.py`,
`approvals.py`, `runtime/`, the audit log and `docs/security/`. Shared files (`CHANGELOG.md`, `docs/bugs.md`,
`docs/missing.md`): `git pull` first, own lines only, keep both on a clash.

## Workflow for every step
1. The builder does `git pull`, writes the failing tests, then the code, until they pass.
2. Full suite (`/home/user/venv314/bin/python -m pytest -q`, or `python -m pytest -q` on the laptop) plus browser tests
   for page changes.
3. Push to `prometheus-v3`; GitHub `windows-latest` run must be green.
4. A different model reviews; findings fixed with a failing test first; tick the step; short report to the owner.
Never force-push; never touch main; ask before installing anything; never fake results.
