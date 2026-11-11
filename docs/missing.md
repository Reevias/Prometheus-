# Prometheus: what is still missing

Updated 11 November 2026. Version 1.4. Built, tested here and on GitHub: 630 tests pass on Python 3.12 and 3.14.
Not yet run end to end on the owner's real laptop.

## Just finished
- **Planning loop**: each step gets up to 3 tries, a different way each time; after actions it checks the goal really happened (app window open, files there); each task stops at $0.20 of paid AI or 12 steps.
- **Opening apps**: installed app first; if missing, its website, with an Install button in the chat; with no website, it offers to install; if it can't be found, the Microsoft Store opens on a search.

## 1. Planning and autonomy
1. **Long goals in the background**: "tidy Downloads every night", "every Monday summarise my notes". Saved goals that run on a schedule, with Pause and Delete.
2. **Learning from its own failed tasks**: when a retry works, write down the lesson so it gets it right first time next time. (Next step.)
3. **Daily summary**: what happened today, what is coming tomorrow, spoken and written. (Chosen at the start, not built.)

## 2. Senses and triggers
4. **Seeing the screen**: understand a screenshot so it can click "the Download button" instead of exact positions.
5. **Camera**: one photo when asked ("what am I holding?"), never always on.
6. **Watchers**: a new file in a chosen folder, battery low, CPU busy, disk almost full; speaks up or starts a goal.
7. **Email and webhook triggers**: "tell me when the bank emails", "Sir, the tests failed on GitHub".

## 3. Tools
8. **MCP support**: plug in ready-made tool servers (Notion, Slack, home devices and more). New servers start off until approved.
9. **Browser automation**: forms, logins, clicking through sites, in its own browser profile; asks before logging in, submitting, paying or posting.
10. **Running code safely**: a sandbox (Windows Sandbox on Pro, otherwise a locked Python) with one scratch folder, no network, time and memory limits.
11. **Gmail, Google Calendar and Drive**: read freely, ask before sending, accepting or sharing.
12. **Phone access**: on home Wi-Fi first (QR code), then anywhere with Tailscale.
13. **Excel and slides** as outputs. (PDF and Word are done.)
14. **Search inside files**: find the file that mentions "invoice 4521", not only by name.

## 4. Memory
15. **Links between facts** (knowledge graph): "Ravi, brother, lives in Pune, birthday 12 March".
16. **Replacing facts that changed**: new phone number or address, the old one kept with a date.
17. **Nightly memory tidy-up**: merge duplicates, drop stale facts.

## 5. Photos and media
18. **Sort photos by what is in them** (beach, people, documents) and read text in images (OCR).
19. **Sort the photos on the phone.**
20. **Make images and videos**: needs a cloud graphics card; the laptop has none.

## 6. Interaction and polish
21. **Hand gestures** with the webcam (finger tracking).
22. **Tray icon** and Windows notifications for everything, not only reminders.
23. **Cost dashboard**: what each AI model has cost.

## 7. Other agents
24. **Hermes agent** on the desktop PC.
25. **Self-edit level 2**: running tools Prometheus wrote itself (needs the code sandbox, item 10).
26. **Antigravity calling Prometheus** the other way round (Prometheus as a tool server).

## 8. Safety still to add
27. **Code sandbox** (with item 10).
28. **Automatic prompt-injection tests** (garak).

## 9. Testing still owed
29. **First full run on the real laptop**: Wi-Fi on non-English Windows, WPA3 networks, voice on the real microphone, real GitHub and Antigravity calls, the reminder pop-up.
30. **Voice tuning** from real use: the wake phrase "Hello Prometheus, what time is it?" woke 6 times in 10 in testing; the others 9 or 10 in 10.
31. **Small leftovers**: one bad model name pauses its whole provider for 5 minutes; Remove can't delete a key set with `setx`; the browser may offer to save an API key as a password.

## 10. On the owner's side
32. **Claude API key** in Settings: this is what makes answers really good.
33. **GitHub token** for the own repository (fine-grained, one repo).
34. **Backups** (File History or OneDrive) on before big file sorting.
35. **2 or 3 exact tasks that went wrong**, to keep as permanent tests.
36. **Ollama running** for private answers (file and note questions use the local model only).

## Suggested order
1. Learning from failed tasks (2)
2. Watchers and background goals (6, 1)
3. Memory links and fact updates (15, 16)
4. Gmail, Calendar, Drive and email triggers (11, 7)
5. Phone on Wi-Fi (12)
6. MCP (8)
7. Seeing the screen (4)
8. Browser automation (9)
9. Code sandbox (10, 27)
10. Daily summary, Excel and slides, search inside files, tray icon, cost dashboard (3, 13, 14, 22, 23)
11. Photos, camera, gestures, images and video (18 to 21, 5)
