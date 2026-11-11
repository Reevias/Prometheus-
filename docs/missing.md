# Prometheus: what is still missing

Updated 11 November 2026. Version 2.0. Built and tested here: 845 tests pass.
Not yet run end to end on the owner's real laptop.

## Just finished
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
12. **Phone access**: on home Wi-Fi first (QR code), then anywhere with Tailscale.
13. **Excel and slides**: done.
14. **Search inside files**: done.

## 4. Memory
15. **Links between facts**: done.
16. **Replacing facts that changed**: done.
17. **Nightly memory tidy-up**: done (with Undo).

## 5. Photos and media
18. **Sort photos by what is in them**: done (tags, text in photos, sorting into folders after a yes).
19. **Sort the photos on the phone.**
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
30. **Voice tuning**: training, better speech models and instant answers are built; still needs your real-microphone test (send the timings from Settings).
31. **Small leftovers**: fixed.

## 10. On the owner's side
32. **Claude API key** in Settings: this is what makes answers really good.
33. **GitHub token** for the own repository (fine-grained, one repo).
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
1. Phone link (12), then phone SMS for the money tracker and phone photos for the homework checker
2. Sorting photos on the phone (19)
3. Camera, gestures, images and video (5, 20, 21)
