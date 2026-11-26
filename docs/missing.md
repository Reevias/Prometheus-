# Prometheus: what is still missing

Updated 11 November 2026. Version 2.3. Built and tested here: 883 tests pass.
Not yet run end to end on the owner's real laptop.

## M6 6.4a Control PC: screen, mouse, keyboard, 25 November 2026
- On the owner's Windows 11 laptop, `EnumWindows` (`list_windows`), `GetSystemMetrics` virtual screen bounds (`(0, 0, 3840, 1080)`), terminal window blocking (`powershell.exe`), `UIAutomationClient` control listing against a test WinForms window (`PromCheckWin.exe`), and `< 1s` Stop all termination of an active PowerShell subprocess ran and passed.
- NOT VERIFIED live on an unlocked interactive desktop: `ImageGrab` screen capture (`OSError: screen grab failed`), `SetForegroundWindow` focus switching (`GetForegroundWindow() == 0`), typing into a live non-password box, and live UIA `IsPassword` refusal (Windows saw only HWND `Pane` wrappers while the screen was locked/asleep during the test session); covered with unit tests using fake Win32/UIA responses.
- `prometheus/app.py` and `prometheus/agents/__init__.py` were left untouched per build boundaries, so registering `pc_control` at startup (before `prometheus.orchestrator.registry` is imported or before `PCControlAgent.run` runs after a restart with pending approvals) still waits on final wiring.

## M6 6.4b control the laptop from the phone, 26 November 2026
- Windows and the phone are faked in the tests: the laptop actions (laptop.py) and the mouse and keyboard (6.4a) run through the existing code, never tried from a real phone. NOT VERIFIED on your laptop or phone. There is no phone app screen for it yet (the phone side is the HTTP API `/api/remote/...` and its tests).
- "Asks on the phone" is two calls: the laptop gives the phone a one-time yes token, and nothing runs without a second call carrying it. Showing the question and the tap is the phone app's job; a phone whose key is stolen could make both calls itself. The mouse and keyboard actions are the ones that also need your yes on the laptop.
- It needs "Access from other devices" on (the laptop's existing host check refuses a phone's address otherwise) and the laptop listening on its Tailscale address. Only the Tailscale addresses and this laptop count as the private link: your home Wi-Fi addresses are refused.
- A typed text is shown in full on the Approvals page (so you can judge it) and so it is also in Approvals' own "requested" log line; the phone-remote log lines hold only its length. Screenshots stay on the laptop (the phone gets their file name, not the picture). Power actions other than lock (shut down, restart), installing apps and joining Wi-Fi are not on the list.
- Pending requests live in memory: a restart drops them and an old laptop approval then says "ask again". The existing test that every route needs a login now says the phone routes must answer 401 or 403 without a key.

## M6 6.2 item 6 Render files and guide, 26 November 2026
- **The always-on cloud copy is not hosted by these files.** They run only the Cloudroom server: Render's free service has one web port, 512 MB, and forgets its files on every restart (so a cloud copy there would lose its password, sync key and AI keys). Reminders still don't ring while the laptop is off. Opus needs to decide how to host it (or a smaller part of it); `docs/cloud-setup.md` says so.
- The plan said "the cloud copy's jobs (reminders, alerts, sync) run there" and the owner's message said `cloud/space/`; the plan's own newer words are Render, so the folder is `cloud/render/`.
- Docker was not run (no engine here, no Render account). Tested: the start command in a clean environment with only `requirements.txt`, health answers and an unsigned request is refused. The laptop's address screen for Cloudroom isn't built yet, so the guide's step 7 says so.
- The uptime-checker step names UptimeRobot and cron-job.org as examples only; their free limits were not checked.

## M6 6.3 Cloudroom storage, 25 November 2026
- Run against a pretend Drive and a pretend Google web server in tests only; never with your real Google Drive, on your laptop or on a Windows machine other than GitHub's. NOT VERIFIED.
- It uses the recovery code you already made for backups (no new key). If backups were never set up, it stops and says so.
- The private notes are the title and messages of each chat left out in Memory → Privacy, stored as they are (sealed). A chat taken out of "left out" later, or deleted, keeps its old stored copy until you remove it on Drive. There is no screen to read them yet: `Storage.lookup_private(chat_id)` does it in code.
- Needs a connection to read the cloud copy back; while an upload is still waiting, it is read from the laptop's queue. Files in the Drive folder are only readable with your recovery code: if you lose it, they can't be opened.
- The Google sign-in used by backups decides what Drive lets the app see; a file you made by hand in that folder is not seen.
- The old "Prometheus Backups" folder is left alone (not moved or deleted) when you switch this on.

## M6 6.2 Cloudroom (items 1–5), 25 November 2026
- Not run on a real Render service yet: tested against the same server code in-process (NOT VERIFIED on Render and on the laptop). The Render files and the setup guide are item 6 (Sonnet).
- Only Python tasks with no internet can move (the free machine has 512 MB; at most 256 MB, 300 s, 5 MB of files per task). Nothing in Prometheus asks to move a task yet; that needs a button or a plan step (later step).
- Cancelling a task that is already running on Render marks it cancelled and drops its result, but the sandboxed program runs to its own time limit there.
- Render keeps everything in memory: after a Render restart its running and finished tasks are gone (the laptop marks them failed, never resends). The one-time signatures are remembered only until a restart; their 5-minute time limit still applies.
- A sandbox is a guard, not a wall: the Cloudroom server adds the key's folder and /proc to the sandbox's closed places, but the task runs as the same user as the server.
- If the laptop restarts before Cloudroom confirmed a task that had files, the task is marked failed (the files aren't kept for a resend).

## M6 6.1 ExecutionRuntime, 25 November 2026
- Only the laptop runtime exists; a move to Cloudroom is refused until 6.2 adds it and wires your yes (the approval check refuses everything for now).
- Cancelling and pausing from the jobs view still go to M5's runner and graphs directly; only starting goes through the runtime (the plan's item 3).
- A job's result is kept in memory for the latest 256 jobs (graph steps keep theirs in their record), so after a restart only graph-step results are known.
- NOT VERIFIED on the owner's laptop.

## M5 done on the branch, 25 November 2026 (report: `docs/reports/M5_REPORT.md`)
Waiting for the owner to merge. NOT VERIFIED on the owner's laptop after the audit fixes: run the laptop check of
`docs/m5/M5_LAPTOP_CHECK.md` again (5 jobs at once, Stop all, restart and Run again, branching, an own agent, the full
`python -m pytest -q` that hung at `f075968`, Prometheus's own memory, a snapshot in Microsoft Edge). The audit was
written by the session that built 5.2 and 5.3, so it is not the independent fresh-session audit the plan asks for.
The Windows CI hang found in the audit can't be proved gone (CI now dumps every thread's stack after 2 minutes and
stops after 40). A running Python thread still can't be stopped from outside, and nothing runs a Resumed plain job.

## M5 5.3 child agents at once, 24 November 2026
- Only graph steps count as child agents for now (own agents join in 5.4). Memory is measured only for the programs a child starts; its own Python work shares Prometheus's memory and shows as "none" rather than a made-up number.
- The memory cap (1.5 GB per child's programs) applies to commands run through the M1 terminal, which no agent uses yet; the code agent's sandbox keeps its own Job Object and memory limit. Since the audit both are ended by cancel, pause and Stop all. On Linux there is no cap, only a process group.
- Under 1 GB free memory, child agents and chat tasks run one at a time (changed by the audit after the laptop check; the plan said none).
- Steps running side by side reserve each paid call's most possible cost first, so they can't go over the task's limits together (fixed 25 Nov); an AI without a model card can't be estimated and counts only once it has spent.
- Windows: checked on GitHub's windows-latest machines only (the cap test runs only there); NOT VERIFIED on the owner's laptop.

## M5 5.2 task graph, 24 November 2026
- A step that is running is told to stop (cancel, pause, Stop all) and stops at its next check, or at its next AI call, sandbox program or terminal command if it never checks; plain Python work in between can't be interrupted (Python can't stop a thread from outside).
- Orchestrator plans run as a graph only with "Run a task's independent steps at the same time" switched on (Features; off until the laptop check). Such a graph can't carry on after a restart (its steps live in memory): it is closed with "run it again", like the task itself.
- No graph kinds of Prometheus's own are saved yet (only the engine and test kinds); the view comes in 5.5.
- NOT VERIFIED on the owner's laptop.

## M5 5.4 own agents, 24 November 2026
- Own agents can use four tools only (web search, read a web page, read a text file, open a file); more tools need their own checked code first. An own agent has no way to write or change files, run programs or use the browser.
- Removing an agent cancels its jobs in the job tracker (task steps titled "name: ..."). A run that is going inside a chat right now, outside the tracker, stops at its next round (fixed 25 Nov).
- Own agents live in the saved settings file; a hand-edited file can only lose power (unknown tools and copied names are dropped when it loads).
- The tool check (`OwnAgent._call`) was reviewed by Opus on 25 Nov: one hole fixed (bug 180: no web after reading a file). The browser tests for the form were written after the page. Not run on the owner's laptop: NOT VERIFIED on Windows.

## M5 5.5 multi-agent view, 24 November 2026
- A plain job that is running as a thread has no Stop or Pause button: Python can't stop it, and a button that only changed the record would lie. Graph steps use "should I stop?" checks, so graphs have all three buttons. A paused plain job has Stop only: Resume would just mark it waiting, because nothing runs waiting jobs yet.
- The agent column of a task step is read from its title ("research: ..."); jobs started elsewhere show their kind.
- The view reads only what it shows every 4 seconds while it is open, and finished jobs older than 30 days are tidied away (bug 192, 25 Nov).
- Browser tests were written after the page (not before): a quick sabotage check made 3 of the 5 fail. Not run on the owner's laptop: NOT VERIFIED on Windows.

## M5 5.1 job tracker, 24 November 2026 (Opus review, bugs 172-179)
Still open, from the review in `docs/m5/M5_PLAN.md`:
- Cancel and pause of a plain job (not a graph step) only change its record; graph steps can be told to stop (5.2).
- Resume of a plain job only moves it to "pending"; graphs resume for real (5.2). The Resume button is 5.5.
- Finished job rows are tidied away after 30 days (bug 192, 25 Nov); until then they stay.
- Not run on the owner's laptop. Windows: not checked by the reviewer; NOT VERIFIED until GitHub's windows-latest run is green for this work.

## M5 5.6 conversation branching, 24 November 2026
Branching creates a new chat copying history up to the selected message. The branch shows where it came from with a link back to the parent chat. If the parent is deleted later, the branch retains its provenance label ("From: <title>") without a broken link. Not yet tested with live PyWebView window click (tested through full client test suite).

## M5 5.7 browser snapshot, 24 November 2026
Not run on the owner's laptop (Microsoft Edge instead of the Chromium used in tests): NOT VERIFIED on Windows until the owner tries it. Snapshots need a recent Playwright (the `mode="ai"` snapshot); `pyproject.toml` still says `>=1.45`, which is older, so the agent says "pip install -U playwright" if it is too old (tested with 1.65.2). Page elements inside iframes are not in the snapshot yet. A page can still lie about its own buttons: the click rules read what the page reports about them. (Error text and button names written by a page are now wrapped as untrusted: bugs 178-179.)

## M3 audit, 19 November 2026

Phases A to D are done (D6 exit check 20 Nov: 3 more bugs fixed, bug log 123-125). Still to do: run it on the owner's laptop and the owner's licence sign-off in `THIRD_PARTY_LICENSES.md`. Billed catalog models need rates on their model cards. Local cost estimates can differ from the provider's invoice.

## M4 review A (M4.1–M4.3, M4.8), 21 November 2026

Five bugs fixed (bug log 150-154). Still open, for the owner or a later step:
- **repo_check calls many normal repos dangerous and deletes them**: a setup script that installs a tool with
  `curl … | sh` (common in `.devcontainer/` and CI files; pallets/itsdangerous has one) counts as "downloads and runs
  code". Changing repo_check's rules is the owner's call; a likely answer is "check this first" for files in
  `.devcontainer/` and `.github/`, which Prometheus never runs. Until then the exit gate's real Python repo has to be
  one without such a script.
- Code maps don't list CommonJS `require(...)` as imports, and TypeScript declaration files (`.d.ts`) show no functions.
- The real download from GitHub (API + codeload) was not run in the review (this cloud session can't reach the GitHub
  API for other repos); real repos were read from their own zips through a fake GitHub.

## M4 merged, 21 November 2026 (owner's instruction)
Still to check on the laptop (NOT VERIFIED on Windows): the window buttons, drag-to-top and double-click on the title
bar; the real skills library (steps in `docs/reports/M4_REPORT.md`); RAM use with voice and photos. The two own-repo
findings below were fixed by the builder session (bug log 159-160).

## M4 final audit, 21 November 2026 (not merged; `docs/reports/M4_REPORT.md`)
- The two Important findings (own repos not wired; your main repo deleted as dangerous) were fixed on 21 Nov (bug log
  159-160); the auditor re-checks them before any merge. Still open there: the old "add repo on screen" path
  (`clone_and_check`, git clone) keeps the strict check for every repo, own or not.
- Not done yet: the exit gate with your real `prometheus-skills` library, and your laptop check of M4.
- Owner's answers (21 Nov): own repos = same GitHub owner; own repos get "check this first" (Defender still deletes
  threats); built by a new builder session (prompt in docs/m4/M4_PROMPTS.md); the real library is tested by the owner
  in the laptop check (steps in docs/reports/M4_REPORT.md), never from the cloud.

## M4 review B (M4.4–M4.7), 21 November 2026

Three bugs fixed (bug log 155-157). Answers to the questions below, and what is still open:
- M4.7's second question can't happen any more: since review A, Accept needs a downloaded and checked repo.
- Still open: Accept and Switch on wait for the whole trial run (73 s for a script that runs forever) with no
  "testing…" on screen; a TOCTOU gap in the trial (the script is hashed, then read again to run it); nothing runs
  skill scripts for real yet, so `still_valid` has no caller outside the switch and publish checks; there is no
  "Publish" button yet (the approval exists, nothing asks for it); a skill whose manifest.json is removed or broken
  switches on without a trial (it then loads as an old-style skill).
- Real repos: drafts from mahmoud/boltons (one script, a joke that loops forever: the trial stopped it, the skill
  stayed off), python-humanize/humanize and keon/algorithms (guides only: no file was self-contained enough).

## M4.7 questions (21 November 2026), for Review B
- Candidates has no "remove" action. If one is added, it must call `knowledge.sync_candidate` so that candidate's notes are forgotten.
- A candidate accepted before it had a downloaded folder gets 0 notes. If it gets its folder later and is already
  accepted, it should be indexed then (one `sync_candidate` call). Review B checks whether this can still happen
  now that M4.4 downloads the repo before a candidate is added.

## M4.6 questions (21 November 2026), for Review B
- Accept waits for the trial run (up to about 3 minutes with three slow scripts). Fine for now; it should show
  "testing…" so it doesn't look stuck.
- A script edited after a skill was switched on is only caught the next time it is switched on. Whatever first runs
  skill scripts for real must call `still_valid` first, and refuse and switch the skill off if it's false.
- The trial uses the locked-down Python, not Windows Sandbox (`sandbox.run()` takes no timeout).

## Just finished
- **One-line install, Prometheus-Setup.exe and Prometheus.apk** (built by GitHub on each release), own icon in Windows search, **simpler Settings with Features on and off**, **Stop all that stays stopped**, **weekly report card**, **practice tests** and **ready-made jobs** (everything from the "Build your first AI agent" guide).
- **Chatbot-level voice**: ElevenLabs hearing (Scribe v2) and speaking (Flash), falling back to the laptop by itself; **double clap** to be greeted with a brief and talk without the name; **talk over it** to interrupt.
- **Phone control** over wireless debugging, `prometheus doctor` version check, restart by itself after updates, and the second security review.
- **Prometheus on the phone** (12): installable app, notifications, hold-to-talk, photos and files, bank SMS, phone photos (19), laptop jobs from the phone.
- **Encrypted nightly backup to Google Drive** (recovery code, `prometheus restore`), **an always-on cloud copy** on Oracle's free machine that syncs with the laptop, **seeing the screen**, and **21 OpenRouter models to choose from** (Kimi K3, Qwen 3.8 2.4T, DeepSeek V4…).
- **Prometheus as a tool for other AI apps** (26): Antigravity, Claude Desktop and Cursor can search its memory, find files by name and ask questions; actions wait on Approvals.
- **"Undo that"**, **study coach** (flashcards, chat and voice quizzes, spaced review, Anki export), **money tracker** (bank/UPI emails, budgets), **clipboard and screenshot memory**, **focus mode** (any length), **weekly laptop health check**, **exam countdown planner**, **lecture to notes**, **read-aloud**, **homework checker** and **assignment tracker**.
- **Own icon and name**: window, taskbar, shortcuts, tray, notifications and browser tab show Prometheus, not Python.
- **Voice fixes**: the AI now always knows the date and time (the "location instead of time" bug); instant answers with no AI for time, date, opening apps and sites, timers, reminders, volume, battery and weather; three speech models in Settings (fast, Indian-accent English, English + Hindi); actions said back before they happen; timings shown in Settings.
- **Tray icon and notifications** (22), **cost page** (23), **own tools** (25), **safety check** (28), **voice training** (30) and the **small fixes** (31).
- **Search inside files** (14), **linked facts that keep their history** (15, 16), **nightly memory tidy-up** (17) and **photos: tags, text and sorting** (18).
- **Daily summary**: morning briefing (08:00) and evening wrap-up (21:00) with weather, calendar, important email, reminders and what Prometheus did.
- **Email alerts and webhooks**: new mail from chosen senders every 5 minutes; a secret webhook address other apps (like GitHub) can call.
- **Browser automation**: its own visible Edge window and profile; pay/order on the Approvals page, sign in/submit/post asked in the chat, passwords typed only by you, Take over button.
- **Code sandbox**: scripts shown and asked first; Windows Sandbox when the laptop has it, otherwise a locked-down Python with no internet (asked per run), no access to your folders or keys, time and memory limits.
- **Google Calendar and Drive**: add and move events (asked first), search and read Drive files.
- **Excel and slides**: tables become .xlsx with totals and charts; notes become .pptx decks with speaker notes.
- **Connections**: email from any provider (app password; reads without marking read; sending waits for approval), calendars through their secret iCal link, and any app with an MCP server (Notion, Slack, Spotify…).
- **Learning from failed tasks**: what failed and what worked is written down and used next time; general lessons become nightly rules.
- **Standing goals and watchers**: scheduled tasks ("every night tidy Downloads"); battery, disk, CPU and folder alerts.
- **Planning loop**: each step gets up to 3 tries, a different way each time; after actions it checks the goal really happened (app window open, files there); each task stops at $0.20 of paid AI or 12 steps.
- **Opening apps**: installed app first; if missing, its website, with an Install button in the chat; with no website, it offers to install; if it can't be found, the Microsoft Store opens on a search.

## 1. Planning and autonomy
1. **Long goals in the background**: done (standing goals).
2. **Learning from its own failed tasks**: done.
3. **Daily summary**: done.

## 2. Senses and triggers
4. **Seeing the screen**: done (window in front, mouse pointer, clicks asked first, add repos from screen with a malware check).
5. **Camera**: one photo when asked ("what am I holding?"), never always on.
6. **Watchers**: done (battery, disk, CPU, folders).
7. **Email and webhook triggers**: done.

## 3. Tools
8. **MCP support**: done (command or web address; changes asked in the chat).
9. **Browser automation**: done.
10. **Running code safely**: done. (The Windows Sandbox path is untested until the real laptop.)
11. **Gmail, Google Calendar and Drive**: done (needs your Google sign-in in Settings).
12. **Phone access**: done (installable app through the cloud copy and Tailscale).
13. **Excel and slides**: done.
14. **Search inside files**: done.

## 4. Memory
15. **Links between facts**: done.
16. **Replacing facts that changed**: done.
17. **Nightly memory tidy-up**: done (with Undo).

## 5. Photos and media
18. **Sort photos by what is in them**: done (tags, text in photos, sorting into folders after a yes).
19. **Sort the photos on the phone**: done (photos sent from the phone are tagged and sortable).
20. **Make images and videos**: needs a cloud graphics card; the laptop has none.

## 6. Interaction and polish
21. **Hand gestures** with the webcam (finger tracking).
22. **Tray icon and notifications**: done.
23. **Cost dashboard**: done (monthly limit, warning at 80%).

## 7. Other agents
24. **Hermes agent** on the desktop PC.
25. **Self-edit level 2**: done (own tools, approved once by their code).
26. **Antigravity calling Prometheus**: done (MCP tool server).

## 8. Safety still to add
27. **Code sandbox**: done.
28. **Prompt-injection tests**: done (Safety check in Settings, run against the real planner).

## 9. Testing still owed
29. **First full run on the real laptop**: Wi-Fi on non-English Windows, WPA3 networks, voice on the real microphone, real GitHub and Antigravity calls, the reminder pop-up, the Edge browser window, Windows Sandbox, Google sign-in.
30. **Voice tuning**: ElevenLabs, the double clap and interrupting are built; still needs your real-microphone test (try two claps from across the room, and talking over an answer on speakers; send the timings from Settings).
31. **Small leftovers**: fixed.

## 10. On the owner's side
32. **Claude API key** in Settings: this is what makes answers really good.
33. **GitHub token** for the own repository (fine-grained, one repo).
33a. **Make the repository public** (GitHub → Settings → General → Change visibility). Needed for the one-line install, and GitHub only builds the installer and the app for free on public repositories.
33b. **ElevenLabs key** in Settings → Voice (the free plan covers only a few minutes a month; the cheapest paid plan is enough for daily use).
34. **Backups** (File History or OneDrive) on before big file sorting.
35. **2 or 3 exact tasks that went wrong**, to keep as permanent tests.
36. **Ollama running** for private answers (file and note questions use the local model only).

## 11. Suggested new features
37. **Study coach**: done.
38. **Money tracker**: done (phone SMS comes with the phone link).
39. **Clipboard and screenshot memory**: done.
40. **Focus mode**: done.
41. **Weekly laptop health check**: done.
42. **Exam countdown planner**: done.
43. **Lecture to notes**: done.
44. **Read-aloud mode**: done.
45. **Homework checker**: done.
46. **Assignment tracker**: done (college emails and what you say; portal websites can come later).
47. **"Undo that"**: done.

## Suggested order
1. First real run on the laptop, the cloud copy and the phone (29, 30)
2. Camera, gestures, images and video (5, 20, 21)
