# Changelog

## 1.4
- Code sandbox: "average my marks", "chart my spending", "convert this CSV": Prometheus writes a Python script, shows it in the chat and runs it after a yes (or "Always run scripts without asking"). On Windows Pro with Windows Sandbox turned on it runs in a throwaway Windows; otherwise in a locked-down Python that can't use the internet, start programs, load DLLs, read your folders or Prometheus's keys, or save outside its own folder, with no keys in its environment, a 60-second limit and a 1 GB memory limit. If a script needs the internet it asks in the chat for that one run. Charts, sheets and other saved files show in Outputs. Your files can be copied in for it to work on.
- Browser control: Prometheus uses websites in its own visible Microsoft Edge window with a separate profile (it never sees your normal saved passwords): open a page, read it (its text, buttons and fields), type and click step by step. Pay, buy or order waits on the Approvals page; sign in, submit, send, post or delete is asked in the chat; passwords are only typed by you on the Approvals page and go straight into the page. "Take over" under the chat pauses Prometheus so you can drive; "Hand back" lets it continue. Needs `pip install -e ".[browser]"` (the installer does it).
- Google Calendar and Drive: sign in with your own Google "Desktop app" client (steps in Settings; PKCE, only a refresh token is kept). See events, add or move them (asked in the chat first), search Drive and read Docs, Sheets, Slides, text and PDFs.
- Excel and slides: budgets, marks and timetables become .xlsx files (each Markdown table a sheet with a bold frozen header, real numbers, a Total row with formulas, and a bar chart); revision notes and presentations become .pptx decks (title slide, one slide per heading, nested bullets, speaker notes, long slides continued). They show in Outputs.
- New-email alerts: every 5 minutes Prometheus checks for new mail from the senders you chose and tells you at once (spoken, notification, alerts chat), each email once.
- Webhook: a secret address other apps can call to make Prometheus speak up; GitHub test runs and pushes are understood ("Tests failed on Reevias/Prometheus- (main)"). A wrong address gets "not found"; Settings can make a new one.
- Daily summary: a morning briefing (08:00) and an evening wrap-up (21:00) with the weather (Open-Meteo, no key), the calendar, important email (chosen senders first, newsletters skipped), reminders and goals, and, in the evening, what Prometheus did. Made on the laptop without an AI call; posted in the "Daily briefing" chat, shown as a notification and spoken. "Give me my briefing" works any time. Times and the weather city are in Settings.
- Standing goals: "every night at 11 tidy my Downloads" runs the task by itself on schedule and posts the result in that chat (waits if the chat is busy); listed under Coming up with a goal tag.
- Watchers: battery below 20% and disk over 90% (on), CPU over 90% for 5 minutes (off), and watched folders whose new files are told to you or handled by a goal ("file new PDFs into Documents/PDFs"). Alerts go to the "Prometheus alerts" chat, a Windows notification and the voice; each is said once until things change.
- Learns from its own tasks: when a step failed and another way worked (or nothing did), Prometheus writes a short lesson, uses matching lessons when planning similar requests, and folds general ones into its nightly rules. Lessons from steps that read personal files never include what was found. Listed on the Memory page with Delete.
- Connections: email from any provider with an app password (check unread, search, read without marking as read; sending waits for approval; private to the local model unless allowed), calendars through their secret iCal link (today, tomorrow, the week, search; repeating events), and any other app with an MCP server (command or web address; read-only tools run, changes are asked in the chat and can be always allowed per app). All in Settings → Connections, behind your password.
- Opening an app that isn't installed: its website opens with an Install button in the chat; with no website it offers to install; if it can't be found, the Microsoft Store opens on a search for it.
- docs/Prometheus-missing.pdf: everything still missing, grouped, with the suggested order.
- Planning loop: when a step fails, Prometheus tries another way (up to 3 tries, never the same try twice, e.g. the website when the app isn't installed); after actions it checks the goal is really reached (the app's window opened, the files are there, the note or reminder was saved) before saying done; each task stops at $0.20 of paid AI or 12 steps. Limits are in Settings. Details from personal files never go into the retry request.
- Approvals, two kinds: small things (an unfamiliar website or file, a click, pressing Enter, switching models) are asked right in the chat with Allow / Always allow this site or folder / Don't allow. A yes is remembered in every chat; the list is in Settings, under Always allowed, with Remove. Big things (installs, power, Wi-Fi, GitHub posts, cloning, self-edits, repo of the day, paid cloud jobs, big Recycle Bin batches) stay on the Approvals page.
- Alarms, timers and reminders: "alarm for 10 mins", "remind me at 6 to call mom", "every day at 8am tell me the weather". They ring with a Windows notification, are spoken if voice is on, appear in the chat, survive restarts (fired at the next start if missed), and are listed under "Coming up" with Cancel.
- PDF output: the files agent writes PDFs from Markdown with headings, lists and clickable links (Word too). Roadmaps and long answers can be saved straight away, with research first if needed.
- Learns how you like answers: 👍 / 👎 on every answer (👎 asks what should be better and rewrites it at once). Each night Prometheus turns the ratings into a short list of rules that every answer follows; the Memory page shows them, with Learn now, Reset and earlier versions to restore.
- Repo of the day: every evening at 23:30 Prometheus reads github.com/trending, picks the new repository gaining the most stars, and puts it on the Approvals page with an Open link button. Approving adds it to the skill library at once (a guide plus its README kept as data); nothing is cloned or run. Nothing is hidden (the owner decides); repositories about tracking people, accounts or security are labelled "sensitive" on the card.
- Updates run every evening after the repo of the day (or soon after start if the laptop was off), with no approval; "update yourself" in chat also runs straight away.
- Outputs: under Notes, every file and folder a chat created, found, moved or renamed, with Open and Show in folder. Documents and media open in their app (notes from the vault open in Obsidian); code and other types are only shown in their folder, so nothing is run by accident.
- Obsidian: connect your vault in Settings (vaults Obsidian knows are found by themselves; needs your password). Search, read and create notes, list recent ones, open a note in Obsidian. New notes never overwrite and can be undone; answers from notes stay on the laptop.
- Dark and light mode toggle in the top bar (remembered on this laptop).
- Text can be selected and copied anywhere in the desktop window.
- GitHub: reads any public repository (give owner/name or a link): what it is, stars, licence, README and main files, commits, issues and files. The GitHub key is only ever sent for Prometheus's own repository; other repositories are read without it (the key, limited to one repository, made GitHub answer "not found"). Changes are still only made to its own repository.
- Updates itself: checks GitHub every 6 hours (first check 90 s after start), installs new versions by fast-forward only and never during a task; the top bar says when a restart is needed, and the background copy restarts itself when idle. "Update by itself" in Settings.
- Voice, warmer and quicker: Kokoro voices Heart (default) or Fable replace Piper; "Yes sir." the moment a command is heard, "Yes sir?" when only the name was said; a command said in the same breath is used straight away (no second listen); Whisper is told to expect "Hello Prometheus"; every new command starts with "Hello Prometheus". Missing voice models download by themselves. Measured on varied spoken takes: 0 false wakes in 50.
- Open apps and sites without asking: "open Spotify" opens the installed app, or open.spotify.com if it isn't installed (same for WhatsApp, Netflix, Discord, Telegram and others). About 40 well-known sites and their search pages (YouTube, Spotify, Google, Gmail, Wikipedia, Amazon…) open straight away; any other site still asks. "Play X on Spotify" opens Spotify's search.
- Microsoft Store: "open the Microsoft Store" (optionally on a search) opens straight away; "install Spotify" finds the exact package in winget or the Store and asks once before installing.
- Script runners and admin tools (mshta, wscript, rundll32, diskpart and more) are never opened.
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
