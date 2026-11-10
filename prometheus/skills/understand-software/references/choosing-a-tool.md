# Choosing between craft-anything and REA

Source: https://github.com/storytold/craft-anything (MIT OR Apache-2.0, as of 2026-11-09) and https://github.com/morluto/rea (MIT, as of 2026-11-09)
Files read: both READMEs, REA DISCLAIMER.md, craft-anything docs/ROADMAP.md.

## Quick decision

| Goal | Use |
|---|---|
| Make my own look-alike of a website or small game | craft-anything |
| Understand how one feature of a desktop/Electron app works, with evidence | REA |
| Look inside a file (APK, DLL, GGUF, SQLite, firmware) | REA |
| Study a website's structure without reading its code | craft-anything (Clean room) |
| Read a website's scripts and network calls | REA (browser analysis) or craft-anything Deep |
| Rebuild automatically with a coding agent | craft-anything (REA does not build; the AI app writes code) |
| Understand a project's own source code | neither; see the explain-code skill |

## Differences

| | craft-anything | REA |
|---|---|---|
| Form | desktop app + CLI + MCP server | MCP server + CLI |
| Language | Rust | Node.js |
| Builds new code | yes, through a coding agent | no, the connected AI does |
| Clean-room mode | yes | no equivalent mode |
| Needs coding agent | yes (Claude Code first) | needs an MCP-capable AI app |
| Windows | installers on Releases | Node works; native engine support experimental |
| Maturity | early alpha | fast-moving, many tools |

## Which is lighter for the owner's laptop

REA's static JavaScript and .NET checks are light. Native decompilation (Ghidra) is heavy for 8 GB. craft-anything from source needs a Rust build; use the installer instead. Neither helps a 2B local model much: both depend on a capable tool-calling agent.

## What neither can do

- Give permission. They do not check that you may analyse a target.
- Bypass DRM, accounts, anti-cheat or online-service protections (both projects refuse).
- Guarantee a perfect copy; craft-anything's checks report differences, not identity.
- Recover original source code or comments; they infer from what remains.
- Run safely on their own. Neither is a sandbox; anything run acts with the owner's permissions.
- Work well with small local models (craft-anything's own notes say local builds are unreliable).
- Analyse server-side code you cannot reach.

## Steps

1. Confirm ownership or permission.
2. Pick from the table; if unsure, start with the lighter static option.
3. Ask the owner to approve install steps.
4. Keep files in a dedicated folder.
5. Report evidence and unknowns, not guesses.

## Not verified

- Comparison is from READMEs only; nothing was run.
- "Neither helps small models" is inferred from the craft-anything roadmap notes and tool-count sizes.
