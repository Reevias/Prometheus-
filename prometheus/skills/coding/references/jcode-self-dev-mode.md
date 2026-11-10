# jcode: self-dev mode

Source: https://github.com/1jehuang/jcode (MIT, as of 2026-11-09)
Files read: README.md, AGENTS.md, docs/CLOUD_AUTO_UPDATES.md (self-dev parts), crates/jcode-app-core/src/tool/selfdev/mod.rs and file names in that folder, src/cli/args.rs, docs/SAFETY_SYSTEM.md.

## What it is

The README says you can tell jcode to "enter self dev mode" and it then changes its own source code. The point is that behaviour can be changed in ways a plugin could not.

## Entry points

- CLI: `jcode self-dev` (alias `selfdev`), with `--build` to build and test a new canary version first.
- In a session: the `selfdev` tool. Outside self-dev it only offers `enter`, `setup`, `reload`, `status`, `find-config`. Inside it adds `build`, `build-reload`, `test`, `cancel-build`, `reload-bridge`, `socket-info`, `socket-help`.
- `--no-selfdev` turns off automatic detection of the jcode repo.

## The loop

1. `enter` starts a separate self-dev session on the shared server (it can clone the public repo with `setup`).
2. The agent edits source files with its normal edit tools.
3. `build` queues a build (`target`: auto, tui or all). Builds are queued, so several agents do not compile at once; other agents can see "why this build is needed" (`reason`).
4. `test` runs a given shell command under a compile lock.
5. `reload` (or `build-reload`, build then reload) restarts onto the new binary. A saved "reload context" carries the task, version before and after, so the agent resumes where it stopped.
6. Status and cancel actions exist.

AGENTS.md warns that a plain `cargo build` proves nothing: the running daemon is a symlink to an older versioned binary until reload, so tests may measure old code. To test safely, build with `--profile selfdev` and run that binary on its own socket (`--socket`) so the shared daemon is untouched.

## Binaries and channels

`builds/stable` (release), `builds/current` (local build), `builds/versions/<version>` (kept, never overwritten), and a canary channel. Keeping old versions makes rollback possible. In reload.rs the code marks a canary as "Testing" and calls a function that rolls back a pending activation if the reload fails or times out.

## Safeguards it has

- Separate canary session and build queue.
- Immutable versioned binaries; rollback of pending activations.
- Destructive-command gate on bash.
- Repo rules: small commits, push as you go, use the owner's git identity, no force-push without permission.

## Safeguards it lacks (compared with what Prometheus wants)

- The agent decides when to reload; no required human approval per change is described.
- No per-change human review of diffs is part of the loop (the docs describe tests, not sign-off).
- The doc's own safety design (SAFETY_SYSTEM.md) is marked "Design" and covers unattended ambient mode, not self-dev.
- Environment isolation is described as "not a security sandbox".

## Lessons for Prometheus's approval-gated self-editing

1. Keep the three stages: edit in a copy, test the copy, switch over. Never edit the running copy.
2. Require owner approval at each switch, showing a diff and test output in plain words.
3. Keep every previous version and make rollback one step.
4. Auto-roll back if the new version fails a startup check.
5. Use a queue/lock so only one build or reload runs at a time.
6. Save "what I was doing" before a restart and restore it after.
7. Test against a separate socket or folder, not the live one.
8. Never let self-edits touch the approval gate or safety code without explicit owner sign-off.

## Not verified

- Behaviour of rollback and canary logic beyond reading names and comments.
- Whether `jcode self-dev` works on Windows with Rust installed.
- The list above of "lacks" is inferred from absence in the files read.
