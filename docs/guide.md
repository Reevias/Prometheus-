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
5. 👉 Finish installing **OpenJarvis**, then tell Claude:
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

9. ✅ Claude writes the **spec** (`docs/spec.md`). You read it and say "ok" or "change".
10. ✅ Claude writes the **build plan**: small steps, each with tests.

## Stage 2 — Build version 1 ✅ built (run it on your laptop: see `README.md`)

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

## What's new in version 3 (M5 and M6, in plain words)

Built on the branch and tested here and on GitHub's Windows machines. **Not yet tried on your laptop**, so please
test each one and tell me what breaks. Everything that can change something on your laptop or phone **asks first**.

**Several things at once (M5)**
- **Agents page → Jobs:** every job and task graph that is running or finished badly, with its real times and
  progress. Stop, Pause, Resume or "Run again" appear only when they really work. It refreshes every few seconds.
- **At most 2 jobs at once** (3 when more than 3 GB of memory is free, and one at a time when memory is low).
- **Your own agents:** Settings → Features → turn on "Own agents", then Agents → "New agent": a name, what it does,
  instructions, and the tools you tick (it starts with none). Pick it by typing `@name`. Opening a file always asks.
- **Branch a chat:** "Branch from here" on any message makes a new chat with the history up to that point.
- **Reading a web page for the browser agent:** it can list a page's buttons and fields (a "snapshot") and click or
  type by those. Paying and sending still ask, and it never types a password.

**The cloud and the phone (M6)**
- **Cloudroom** (a free Render service, no card): a task can run there only after you say yes on the Approvals
  page. Setup is in [`cloud-setup.md`](cloud-setup.md). The screen to enter its address and a button to move a task
  are not built yet. **Reminders do not ring while the laptop is off yet.**
- **Cloudroom storage:** Settings → Features → "Cloudroom storage". Your repo knowledge, sync data and the private
  notes of chats you left out are scrambled on the laptop and kept in a "Prometheus Cloudroom" folder on Google Drive.
  You need your backup recovery code to open them. Without internet they wait and go later.
- **Prometheus using your screen, mouse and keyboard:** it can look at the screen and click, type, press keys or
  switch apps on the laptop. Every click, typing or key asks first and shows what it will do. It never types into
  password fields. Stop all stops it within a second.
- **Control the laptop from the phone:** Settings → Features → "Control the laptop from the phone", then System →
  Devices → "New pairing code" and type it on the phone within 5 minutes. Only a paired phone works, only over your
  private link (Tailscale), and every action asks on the phone first. Mouse and keyboard actions also ask on the laptop.
  There is no phone screen for it yet: it works through the phone side's web address.
- **Controlling your Android phone from Prometheus:** Settings → Connections → Phone control, with the phone plugged in
  and USB debugging on. Reading the screen asks once; taps, typed text and changes ask every time.

---

## Safety rules you should know (short)

- Prometheus only opens on your laptop unless you turn on phone access.
- Never port-forward your router. Use Tailscale for outside access.
- Never paste keys in chat. Keys live in Windows environment variables.
- Prometheus never deletes — Recycle Bin + undo.
- Prometheus only sees folders you allow.
- Code from GitHub runs only in the sandbox, and only after you approve.
- Prometheus cannot edit its own safety code.
- Anything that clicks, types, or controls your laptop or phone asks first; Stop all stops it.
