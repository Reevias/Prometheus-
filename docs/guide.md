# Prometheus — Step-by-Step Guide

Simple order of everything, from today to the phone app.
Details for each step live in [`brief.md`](brief.md).

✅ = done · 👉 = next · ⬜ = later

---

## Stage 0 — Your laptop is ready

1. ✅ Install Ollama.
2. ✅ Download the small local model: `ollama pull qwen3.5:2b`.
3. ✅ Revoke the leaked Ollama key.
4. ✅ Get the Claude API key and save it:
   `setx ANTHROPIC_API_KEY "your-key"` → close and reopen PowerShell.
5. 👉 Finish installing **OpenPrometheus**, then tell Claude:
   - does it start?
   - does it work with Claude?
   - how much RAM does it use? (Task Manager → Memory)
6. 👉 Turn on **backup** (Windows File History or OneDrive) before Prometheus sorts real files.
7. 👉 Answer the open questions:
   - What is **Argon**?
   - How do you run your **Hermes Agent**?
   - Which **folders** may Prometheus touch? (e.g. Downloads, Documents, Pictures)
   - Monthly **Claude spending cap**? (e.g. $5 / $10 / $20) — also set it in the Anthropic console.
8. 👉 Turn on **2-factor login** for GitHub, Anthropic, Ollama and Google.

## Stage 1 — Plan on paper (Claude)

9. ✅ Claude writes the **spec** (`docs/prometheus/spec.md`). You read it and say "ok" or "change".
10. ✅ Claude writes the **build plan**: small steps, each with tests.

## Stage 2 — Build version 1 ✅ built (run it on your laptop: see `prometheus/README.md`)

Claude builds and tests each piece here, then pushes it to GitHub.

11. ✅ **Skeleton**: the `prometheus/` folder, settings, login page, logs.
12. ✅ **Safety first**: folder allowlist, permissions, undo journal, Recycle Bin, cost cap, kill switch.
13. ✅ **Shared memory**: tasks, agent notes, chat notes, global memories (+ the memory manager screen).
14. ✅ **Lead AI (Claude)**: plan → pick agent → run → combine.
15. ✅ **Agent 1 — File & Photo Sorter**: facts first (date, type, duplicates); content tags (SigLIP 2) and OCR (EasyOCR) come in v1.1.
16. ✅ **Agent 2 — Web Research** (built-in DuckDuckGo search + page reader; GPT Researcher plugs in later).
17. ✅ **Agent 3 — Document Helper** (PDFs, notes).
18. ✅ **Repo Scout**: finds GitHub repos for a task → waits for your approval → clones into a quarantine folder and never runs the code.
19. ✅ **Self-editing (levels 1+2)**: safe patcher with the 6 checks + your approval.
20. ✅ **Browser UI**: chat, live status, approvals, memory manager, undo history.

After each piece, you update and test on your laptop:

```
git pull
```

21. ✅ **Final v1 test** (automated end-to-end test passes; run it for real on your laptop): "sort my Downloads folder and summarise any PDFs about taxes".

## Stage 3 — Version 1.1 (right after v1)

22. Voice: "Hey Prometheus" wake word, speech-to-text, spoken replies.
23. Gestures: thumbs up = approve, open palm = stop, swipe = next, wake/mute gesture.
24. Phone in the browser at home (password).

## Stage 4 — Later

25. Tailscale: use Prometheus from your phone anywhere.
26. More agents: image/video generation on free cloud GPUs (HunyuanVideo via Wan2GP), Hermes, Argon, your connectors.
27. Real Android app: your phone's files and photos, notifications, voice.
28. If the laptop is too slow: move the planner/research part to a cheap cloud server.

---

## Safety rules you should know (short)

- Prometheus only opens on your laptop unless you turn on phone access.
- Never port-forward your router. Use Tailscale for outside access.
- Never paste keys in chat. Keys live in Windows environment variables.
- Prometheus never deletes — Recycle Bin + undo.
- Prometheus only sees folders you allow.
- Code from GitHub runs only in the sandbox, and only after you approve.
- Prometheus cannot edit its own safety code.
