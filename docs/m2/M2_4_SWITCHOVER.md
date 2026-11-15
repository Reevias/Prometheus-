# M2.4: switch-over checklist (old screens → new look)

Before the old 2.x screens were deleted, every feature they had was checked to have a home in the new look.
Old addresses still work: they send you to the new place (so bookmarks, notifications and the phone app keep working).

| Old screen / feature | New home | Old address now |
|---|---|---|
| Chat (messages, send, files, photos) | `/c/<id>`, ask box with + menu | `/chats/<id>` → `/c/<id>` |
| Chat list and search | Chats page (`/v3/chats`) and the sidebar | removed |
| Rate an answer (👍/👎, 👎 rewrites it) | buttons under each answer | kept, same address |
| Yes/no questions inside a chat | the same cards in the new chat | kept, same address |
| Chat notes | Notes button at the top of a chat | moved to `/api/v3/chats/<id>/notes` |
| Files made in a chat (open, folder) | files strip beside the chat | kept: `/outputs/<id>/open` |
| Reader panel (pause, back, skip, speed, explain, stop) | live card in the chat while reading | `/api/v3/reader/<action>` |
| Browser control (stop, carry on) | live card in the chat while browsing | `/api/v3/browser/toggle` |
| Reminders list, cancel | Automations | `/v3/automations` |
| Status (what it's doing now) | right panel (laptop), Tasks, top bar | removed |
| Approvals | Approvals page | `/approvals` → `/v3/approvals` |
| Memory: saved facts, search, documents, topics, lessons, wipe | Memory page tabs | `/memory` → `/v3/memory` |
| Skills, repo of the day, own tools | Skills page tabs | `/skills` → `/v3/skills` |
| Study: quizzes, quiz sheets, Export to Anki, flash cards | Research → Study | `/study` → Research, Study |
| Exams and deadlines (plans) | Research → Study | `/plans` → Research, Study |
| Money tracker (add, scan a bill, Excel) | Research → Money | `/money` → Research, Money |
| History with undo | System → Activity | `/history` → System, Activity |
| Costs | Settings → AI models and keys → Costs | `/costs` → Settings |
| Settings | Settings in the new look (same sections) | same address |
| "Get it on your phone" | its page in the new look | same address |
| Voice pill (mute) | voice controls in the top bar / ask box | `/api/v3/voice/mute` |
| Phone: notifications | bell in the top bar | same push service |
| Phone: hold to talk, answer read aloud | mic button in the ask box (phones) | — |
| Phone: install to home screen | same app manifest and service worker | — |
| Light theme | **retired**: the new look is dark only (the owner's design) | — |

Deleted: 28 old templates, `static/phone.js`, about 440 lines of old routes. Kept and shared with the new
Settings: `settings_sections.html`, `settings_script.html`, `head_select.html`, `voice_train.html`.
The login page still uses `static/style.css` and `static/orb.js`.

Phones (the Android app and phone browsers) now get the new screens, laid out for a small screen: the menu is a
drawer, the right panel is hidden (System → Devices has the health of each device), and the ask box puts its buttons
under the text. A phone-width browser test checks this. **NOT VERIFIED on a real phone or on the owner's laptop.**
