# Changelog

## 1.4
- Voice: say "Prometheus" (or "Hey Prometheus") and then what you want; it answers out loud with a short version and puts the full answer in that day's Voice chat. All on the laptop: Vosk listens for the name, Whisper base.en confirms it and writes down the command, Piper (British "Alan" voice) speaks. Two-stage wake word, tested on spoken audio: "promising", "the problem" or talk about Prometheus don't wake it. Saying just the name gives a chime, then it listens. A mic pill in the top bar shows listening, thinking or speaking and mutes with a click; the orb brightens while listening. `prometheus voice-setup` downloads the models, `prometheus autostart on` starts it with Windows without a window (`prometheus background`), and opening the app joins the running copy.
- Create new files: text, Markdown, CSV, code and Word (.docx, with headings and lists from Markdown). Never overwrites, can be undone, and never writes file types Windows runs on double-click. A step's text can be saved straight to a file ($stepN.summary).
- Choose the brain: a "Brain" picker in every chat and in Settings lists the AIs that are set up (Claude Opus, Sonnet or Haiku, free AIs, the local model). The chosen one plans and answers simple questions; if it is down, only free or local AIs take over, never a paid one. "Auto" keeps the old behaviour.
- Files anywhere: a Files agent finds files by name, pattern, kind, date or size across the user folder and any drives or folders added in Settings ("Places Prometheus may use", needs your password), lists folders, shows sizes, and copies, moves, renames and makes folders. Every change can be undone on the History page (undoing a copy sends the copy to the Recycle Bin). Moving between drives copies and keeps the original. More than 10 items to the Recycle Bin waits for approval. System folders, browser data, passwords, .ssh and Prometheus's own files stay blocked everywhere.
- AI keys, simpler: one searchable list of every AI (Claude, OpenRouter, Ollama and the free ones from awesome-free-llm-apis). Paste a key and that AI is on at once; Prometheus picks its model (strongest chat model the provider lists, never embedding or speech models) and moves to the next model by itself if one is retired. Free AIs that need no key are on by default (one switch turns them off; never used for personal files). Model fields moved under "Advanced".
- The orb: an ember core inside a turning wireframe globe, with orbiting sparks and flickering rays. Big on the login screen and on an empty chat, small in the status panel; it speeds up while Prometheus works and turns grey after STOP ALL. One light canvas, capped frame rate, pauses when hidden, still for reduced motion.
- Smarter chat names: a quick name from the first message (greetings and "can you" dropped), then a short specific name from the planner during the first two requests. A name you choose is never changed.
- Rename and delete chats (delete removes the chat's messages, notes, steps, memory parts and its file in the Prometheus memory folder; a running chat can't be deleted).
- Search chats: the box above the chat list finds words in any message or chat name, shows the matching lines and jumps to the exact message.
- Chat memory by topic: the planner names the topic of every request; each chat is split into topic parts, saved as chunks, and parts on the same topic are linked across chats. A new request fetches only the few matching parts of earlier chats, never whole chats; private answers are never fetched for cloud models.
- Every chat is also saved as files in the "Prometheus memory" folder in your home folder (chats/, topics/, index.md). Agents can read it but never move or change it.
- Memory page lists topics; each topic page links every chat about it.
- Wikipedia agent (free, no key) for encyclopedic facts; web research falls back to Wikipedia when search fails. Articles are saved as chunks so later questions reuse them.
- Memory chunking: long pages and files are saved as small overlapping chunks inside bigger parent chunks; search finds the small chunk and the model reads the parent. Word search plus optional meaning search with a local embedding model.
- Research and Documents agents send only the passages that answer the question; the planner sees saved web passages.
- Memory page lists saved pages and files and can delete them.
- `scripts/install-windows.ps1`, `prometheus shortcut` and `prometheus update`, so the Prometheus shortcut always opens the newest copy; `prometheus doctor` warns about an old copy.
- Learning-roadmap skill built from curated lists (OSSU, developer-roadmap, free-programming-books and more).
- Secret check: API keys and tokens are blocked from GitHub posts and self-edits.

## 1.3
- Works on Python 3.12 and newer (tested on 3.12 to 3.14).
- Fixes from real use: "open youtube" works; the planner must fill required inputs; switch models by chat (after approval); older chat turns are summarised so long chats keep their context.
- GitHub connection: read the repository; issues, pull requests and self-update after approval.
- Laptop control: apps, windows, volume, brightness, dark mode, screenshots, typing, clicking, power, installs.
- A brain that answers plain questions directly, remembers the conversation and uses Claude plus cheap helper models.
- New graphite interface with a red status signal.
- Polished chat: formatted answers, a line naming what Prometheus is doing, no flicker, chats named from the first message.
- System agent: Wi-Fi, Bluetooth, location and battery, using a fixed list of actions; passwords are typed into the Approvals page and go straight to Windows.
- Antigravity agent through the Gemini API.
- Repository: MIT licence, tests on GitHub for Windows and Linux, contributing and security guides.

## 1.2
- Skill folders (`SKILL.md` + `references/`) written from the reference projects, a Guide agent and a Skills page.
- Catalog of free AI providers with a searchable settings list; three work without a key.
- Memory recall: word forms and partial words match, better and newer matches first, each memory records its source.

## 1.1
- OpenRouter provider; API keys saved from Settings.
- Automatic model switching with a 5-minute rest for failing models.
- Opener agent: YouTube (play and summarise from captions), GitHub, websites, files.
- Desktop window (`prometheus app`) and the red ember theme.

## 1.0
- Lead AI that plans, picks agents, runs steps and combines one answer.
- File Sorter, Documents, Research and Repo Scout agents sharing one memory.
- Safety: path rules, undo journal and Recycle Bin, permissions, approvals, spending cap, kill switch.
- Approval-gated self-editing and a password-protected browser interface.
