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
