# Security audit of Prometheus (16 November 2026)

**Method.** The Claude-BugHunter vulnerability classes (github.com/elementalsouls/Claude-BugHunter, MIT) used as a
white-box checklist on Prometheus's own code. Five independent read-only reviewers, one group of classes each:
files and paths; XSS/CSRF/login; commands, SSRF and code running; SQL, secrets and private-data leaks; prompt
injection, approvals and MCP. A bug counted only if confirmed by reading the code or a failing test. Then, for each:
a failing test, the fix, the test passing (all 1281 tests pass; browser tests re-run).
Not run on your laptop: **NOT VERIFIED on Windows** beyond GitHub's Windows test runner (a few Windows-only effects
below were reasoned from documented behaviour, not run).

**Good news.** No bug let someone from outside reach Prometheus. The login, CSRF, file-path checks, SQL queries,
password hashing, backups and the approval system all held up. Every route without a login was the intended one.

## Fixed (bug log 81-95)
| Area | Problem | Fix |
|---|---|---|
| Privacy | Translate used a cloud AI for text from your private files when the laptop AI was down | never for file text |
| Privacy | Quiz/exam answers from private notes were stored as not private, then sent to the cloud planner | stored private |
| Privacy | Scripts/tools given your files printed results that could reach a cloud AI | private when given files |
| Code runner | A script could run any command through `ctypes` (escaped every sandbox rule) | dangerous calls refused |
| Code runner | The approval cut the script at 1500 characters (hidden second half) | whole script shown |
| Files | Copying a folder copied protected files inside it out (.ssh, Prometheus's keys) | protected places skipped |
| Files | Rename accepted odd names (`a.exe.`, `a.txt:x`, reserved names) | refused; new name gets the checks |
| Approvals | MCP request showed 300 of up to 2000 characters | all shown |
| Approvals | Browser password approval didn't name the site | names it, re-checks it |
| Web | Cloud copy: one device guessing locked everyone out; cookies lacked `Secure` | per-device count; Secure on https |
| Web | `</UNTRUSTED>` in capitals closed the outside-text wrapper; MCP address was a prefix match; Obsidian reply unescaped | fixed |
| Bug | Chat export crashed for non-Latin titles | one safe download-name helper |

## Found but NOT fixed (your call)
Each is small, needs something unusual, or is a design choice. Say which you want.
1. **Move/copy of an existing `.bat`/`.ps1`** skips the "never makes runnable files" rule (low).
2. **Recycle >10 files needs a yes, but a plan can split it into steps** of 10 (recoverable from the Recycle Bin).
3. **"Update yourself" needs no approval** (your earlier choice): a prompt-injected plan could trigger it.
4. **Antigravity spending cap** is checked only when a job starts, so several approved jobs could pass it.
5. **Cost under-counted** (up to 2x) on the few calls that retry after the AI spent its room thinking.
6. **Obsidian**: a note that is a link to a protected file would be read.
7. **Secret scanning** doesn't know the MCP/webhook/sync tokens (only the named API keys).
8. **File permissions**: `.env` is made owner-only after it is written (tiny window); restored backups and
   `memory.db`/`settings.json` aren't owner-only (matters on shared Linux machines, not your Windows laptop).
9. **Hook web address contains its token**, so the foreground `serve` access log prints it.
10. **Sync to the cloud copy** sends flashcards made from private notes (the cloud copy is yours; a design choice).
11. **Phone/browser click approvals** don't re-check the screen when you answer; **a pending approval can still be
    approved while Stop all is on**; repo scout can queue up to 5 clone approvals per MCP call.
12. **Calendar link** follows redirects with no private-address check (only if you set a hostile link).
13. **Cloud copy can queue laptop jobs** with `read_only` off (normal approvals still apply): a trust boundary to
    document.
