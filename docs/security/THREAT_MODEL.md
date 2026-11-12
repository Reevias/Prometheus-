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
