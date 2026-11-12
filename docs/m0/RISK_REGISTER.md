# M0 risk register (M0/M1 scope)

Open risks, each with what limits it today. "Verified" means tested; anything only reasoned about says so.

| ID | Risk | Impact | Today | Status |
|---|---|---|---|---|
| R-1 | Native Windows evidence comes from GitHub's `windows-latest` machines (NTFS, Python 3.14/3.15), not the owner's laptop | A laptop-specific difference (antivirus holding files, OneDrive folders, 8.3 names on) could behave differently | Windows tests run on every push; sharing violations, junctions, attributes and kills are tested there | Open until run on the owner's laptop |
| R-2 | OneDrive "Files On-Demand" folders are reparse points | Edits in Desktop/Documents could be refused, or a cloud tag mistaken for a link | Only junction and symlink tags are refused (`_win_replace_pinned`) | Reasoned, not tested with real OneDrive |
| R-3 | An edit replaces the file with a new one | Explicit (non-inherited) Windows ACLs on that file are not copied; hidden/system/archive attributes and POSIX mode/owner are | Most user files only have inherited ACLs | Open (documented) |
| R-4 | A secret in an unknown format, passed to a command under an innocent name | It would reach the command | Secret-like names, known key patterns and saved keys are refused; program-changing variables are refused | Accepted residual risk |
| R-5 | `journals/transactions.jsonl` grows without limit | Slower startup recovery over months | One line per edit step | Open (rotation is M7 audit-log work) |
| R-6 | The optional Piper voice imports `piper`, which is not declared or installed | Choosing it would fail to start | Not offered by the installer | Open (2.x voice, outside M0/M1) |
| R-7 | The retry queue's "already running" set is per process | Two Prometheus processes at once could both resume the same request | The app runs as one instance | Accepted |
| R-8 | macOS handle paths (`F_GETPATH`) | Untested: no macOS runner | Fails closed if the path can't be read | Not verified |
| R-9 | `safe_replace_with_backoff` (path-based replace) still exists | Could be used by mistake later | No production code calls it; tests fail if the edit/rollback paths ever use it | Accepted |
| R-10 | Edit backups sit in the shared `backups` folder and go into the nightly encrypted Drive backup | Copies of edited files are kept in the backup | Encrypted (AES-256-GCM) like everything else | Accepted |
