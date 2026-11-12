# Prometheus 3.0: Threat Model & Security Architecture

## Trust Boundaries
- Prompt text alone is NOT a security boundary.
- All repository, web, and tool output is treated as untrusted data.
- Policy enforcement must happen outside the LLM in the tool dispatcher and OS sandbox.

## Defense Tiers
1. **Tier 1 (Process Guard)**: Windows Job Objects, restricted tokens/AppContainer where feasible.
2. **Tier 2 (Linux Sandbox)**: WSL2 + rootless Podman/containers, read-only mounts, seccomp profiles.
3. **Tier 3 (Remote Worker)**: Cloudroom worker / microVM with strict network and filesystem policy.

## Security Rules
- Read-only repository intake (never run scripts or build hooks during inspection).
- Defense against path traversal, symlink escapes, and TOCTOU races.
- Action journaling with atomic rollbacks.
- Global persistent STOP ALL killswitch.

## Residual risks (M1, updated 12 Nov 2026)
- **File replacement on Windows (finding 5).** On Linux and macOS the final rename is done relative to an open handle
  of the target folder (`replace_pinned`), after checking that handle really sits inside the task folder, so a folder
  swapped for a link after the check can't redirect the write. Python has no folder-relative rename on Windows, so
  there the replace is path-based, right after every parent folder is re-checked for links and junctions, and the
  file is re-checked through its own handle afterwards (a wrong result is rolled back). A small window remains on
  Windows between the parent check and the rename. Closing it fully needs `SetFileInformationByHandle`
  (FileRenameInfo with a pinned RootDirectory handle); that is planned for M7 and must be tested on real Windows.
- **Environment overrides (finding 1).** Override names containing KEY/TOKEN/SECRET/PASSWORD/AUTH/CREDENTIAL/PRIVATE,
  values matching a known secret pattern, and values containing a saved key are refused. A secret in an unknown
  format under an innocent-looking name can't be recognised; callers must still not pass secrets on purpose.
