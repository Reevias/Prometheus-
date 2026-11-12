# M1: fixes for the five open audit findings (12 Nov 2026)

Evidence: `tests/test_v3_m1_findings.py` (35 tests) plus the existing M1 tests; full suite run on Linux, Python 3.14.
Windows-specific code paths (Job Object, thread resume) are tested by simulating Windows on Linux; they still need a
run on real Windows before M1 can be accepted.

| # | Finding | Fix | Tests |
|---|---|---|---|
| 1 | Caller env overrides bypassed secret filtering | `overrides_allowed`: secret-like names, secret-pattern values and saved keys are refused (`ValueError`) | `test_override_*` |
| 2 | Thread-resume failure left a suspended process | `resume_process_threads` checks every OpenThread/ResumeThread and raises if none resumed; `execute` then kills the process, closes the job and raises. A failed spawn also closes the job | `test_resume_failure_*`, `test_spawn_failure_closes_job` |
| 3 | `read_window` loaded the whole file | one streaming pass for hash/encoding/newline, then lines are streamed and only the window is kept (same line numbering as `str.splitlines`) | `test_read_window_*` (21 MB file stays under 4 MB peak) |
| 4 | Journal paths and backups not validated | journal paths must pass the path policy and match the expected names/folders; a backup is restored only if its SHA-256 matches; files changed since are left alone and reported; rollback copies (keeps the backup) | `test_recovery_*`, `test_rollback_*` |
| 5 | Pathname replacement TOCTOU | `replace_pinned`: rename relative to a checked folder handle (dir_fd on POSIX, NtSetInformationFile on Windows) | `test_replace_pinned_*` |

Other bugs found while checking:
- **Critical:** on Linux/macOS, stopping a timed-out or cancelled command killed Prometheus's whole process group
  (Prometheus itself). The terminal tests killed the test runner. Commands now start in their own session and only
  their own group is ever signalled.
- System uptime off Windows was really "time since Prometheus opened" (0 at start); on Windows the 64-bit tick count
  was read as 32-bit and would go negative after about 25 days.

## Second independent audit (12 Nov 2026): all 5 findings verified fixed; 16 new issues, all fixed

| Sev | Issue | Fix |
|---|---|---|
| High | Env overrides could change which program runs (PATH, LD_PRELOAD, GIT_*, NODE_OPTIONS, ...) | refused (`HIJACK_NAMES`/`HIJACK_PREFIXES`); failing to check saved keys now refuses |
| High | `read_window` had no character limit (200 MB one-line file: 400 MB peak) | `MAX_WINDOW_CHARS` (1 MB), pieces joined once; now 4 MB peak |
| High | Edits reset file permissions (0700 → 0644, 0600 → 0644) | `copy_file_metadata`: mode/owner on POSIX, hidden/system attributes on Windows |
| High | Cancelling the task left the command running | `finally` stops the whole tree |
| Medium | Background child kept a finished command "running"; kill aimed at a reaped pid | group id recorded at start; exit detected from the exit code (asyncio's `wait()` also waits for pipes since 3.12); leftovers killed |
| Medium | OneDrive folders refused on Windows (cloud reparse tags) | only junction/symlink tags refused |
| Medium | Handle path check failed open for paths over 1024 characters on Windows | 32768 buffer, fails closed |
| Medium | Recovery ran without the file lock and could undo a running edit | per-file lock; a STAGED entry never triggers a restore |
| Medium | Files with mixed line endings were rewritten | only the changed text gets the file's line ending |
| Medium | Diff parser: `+++i;` / `-- comment` taken as headers, counts unchecked, no-newline marker ignored | strict parser with exact counts; marker applied; pure insertions placed after their line |
| Low | UTF-8 character split between reads; secret split between pieces | incremental decoder; long lines cut at spaces |
| Low | Restore after a failed check was path-based | goes through the verified, pinned restore |
| Low | Overlapping matches counted as unique | checked with a second `find` |
| Low | macOS handle paths | `F_GETPATH` |
| Low | 64-bit handle restypes; folder could be renamed while pinned | `HANDLE` restypes; no `FILE_SHARE_DELETE` on the pinned folder |

Also found by the Windows CI run: `filelock` (used by atomic_fs) was missing from the requirements, so a fresh install
couldn't edit files. Added to `pyproject.toml`.
