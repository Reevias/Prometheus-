# Prometheus 3.0 — M6 report (runtime, Cloudroom, PC and phone control)

27 November 2026, branch `prometheus-v3`, merged into `main` on the owner's instruction. Everything below is **NOT
VERIFIED on the owner's laptop and on a real Render service** unless it says otherwise.

## What M6 gives you
- **One way to run every job (6.1):** every task starts through `prometheus/runtime/`. The laptop (`LocalRuntime`)
  is the default; each job's steps are numbered events in order.
- **Cloudroom (6.2):** your free Render service (no card) can run a Python script for you. Ask for it ("work this out
  in Cloudroom"); the Approvals page shows exactly what goes up (the code and any files) and what it may do there (no
  internet, at most 300 seconds and 256 MB). Only after your yes does it go up; the answer comes back to the chat;
  Stop ends it on Render too. Set up: Settings → Connections → Cloudroom (Render) and `docs/cloud-setup.md`.
- **Cloudroom storage (6.3):** repo knowledge, backups, sync data and the private notes of left-out chats are sealed
  on the laptop (AES-256-GCM) and kept on Google Drive; the laptop copy wins; works offline.
- **PC control (6.4a):** reads the screen (Windows UI Automation), clicks, types and switches apps; every action asks
  first; never types into password boxes; Stop all stops it within a second.
- **Phone controls the laptop (6.4b):** a paired phone, over the private link only, can run laptop actions; each asks on
  the phone, and mouse/keyboard ones also on the laptop.
- **Phone control through mobile-mcp (6.5):** replaces the old adb code; taps, typing and app changes ask every time;
  reading the screen after a one-time yes.

## Exit gate
| Gate item | Result |
|---|---|
| Disconnect/reconnect | Pass in tests (lost link, lost answer, link down, Render restart, laptop restart before/after the receipt, proxy errors). NOT VERIFIED on Render. |
| Works offline | Pass in tests (storage queue). NOT VERIFIED on the laptop. |
| Encryption | Pass (6.3 tests; code read in the audit). |
| Phone control via mobile-mcp with approvals | Partly: on the owner's OnePlus Nord 5 (Android 16) the phone was found, apps listed, Calculator opened, a screenshot taken, 36 controls read, one tap and Home. Typing, several taps, password refusal: NOT VERIFIED live (the phone locked). |
| GitHub Windows CI | Green (see the CHANGELOG line of the merge). GitHub's machines, not the owner's laptop. |
| Open-source check | Pass: no new Python package; mobile-mcp 1.0.20 (Apache-2.0) pinned, separate program, in Credits. |
| Audit | Done by the Opus session that built 6.1/6.2, with a separate reviewer's second look at 6.1/6.2 (`docs/reports/M6_AUDIT.md`); the owner chose not to have a further independent session. |

## Bugs fixed in M6
199–218 (review of 6.3–6.5), 219–232 (audit), 233–236 (the audit's open items). See `docs/bugs.md`.

## Your part (what you need to add)
1. A Render account (free, no card) and the service from `cloud/render/render.yaml` (`docs/cloud-setup.md`, steps 1–5).
2. Your Cloudroom key as a Render **Secret File** named `CLOUDROOM_TOKEN`, then Deploy.
3. The Render address and the same key in Settings → Connections → Cloudroom (Render) (advanced), with your password.
4. Optional: a free uptime checker visiting `<address>/v1/health` every 10 minutes (keeps Render awake).
5. Optional: Tailscale on the laptop and phone, then pair the phone (System → Devices) to control the laptop from it.
6. A laptop check of M6 (PC control, phone control, a first real Cloudroom task) when you have time.

## Still not done (honest)
- Nothing has run on a real Render service yet.
- On a Linux CPU other than x86-64 or ARM64 the sandbox's program block isn't installed (Windows uses its Job Object).
- Phone screenshots for "look" default to a cloud vision AI (older behaviour; paid AI still asks first).
