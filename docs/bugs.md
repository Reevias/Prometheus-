# Bug log

Every bug found and fixed, newest first, so the count is always exact. Add a line for each new one.

**Total fixed: 80** (as of 15 November 2026)

## 15 November 2026, M2.4 bug hunt (14)
An independent review of M2.3 and M2.4 before the merge. Each fix has a test that failed before it.

| # | Bug | Fixed by |
|---|---|---|
| 80 | Run again on a task waiting to retry after an AI limit ran it now and again later | Run again takes it off the retry list |
| 79 | A project folder that couldn't be made gave a server error | a plain message |
| 78 | Windows-reserved names with an ending (con.txt) and line breaks could reach project and report file names | names are cleaned the same way everywhere |
| 77 | A project's file list wasn't really newest first in big folders, and could list places Prometheus may not use | looks at more files before sorting; blocked places left out |
| 76 | A too-big project file said "not a project" | it says it's too big |
| 75 | File names from a watched folder, kept in the Automations chat, reached later requests as ordinary text | replayed marked as outside text, never obeyed |
| 74 | Hold to talk while Prometheus was answering stopped the answer; a question spoken on Home was never read out | spoken questions queue like Enter; the new answer is read out, also in a new chat |
| 73 | Folder boxes refused Explorer's "Copy as path" (quotes) and bare names like Downloads | read like Settings' places |
| 72 | A chat could be taken out of a project it wasn't in | refused |
| 71 | Emptying a project's instructions didn't clear them (and an empty rename said Saved) | an emptied box clears; a rename needs a name |
| 70 | Downloading a project with a Hindi name failed (server error) | the file name is sent the standard way for any language |
| 69 | Approving an automation ran the text from when it asked, even if you had edited, paused or deleted it since; the card didn't show what it would do | it reads the automation again at your yes, and the card shows it |
| 68 | An imported project's instructions, notes and chats were treated as the owner's own words | they are outside text until you edit the instructions yourself |
| 67 | Notes in a chat: Add and Delete did nothing (the popup swallowed the clicks) | the buttons reach the shared handler; a browser test adds and deletes a note |

## 15 November 2026, M2.4 (2)
| # | Bug | Fixed by |
|---|---|---|
| 66 | Phone width: the send button fell onto a third row of the ask box (the AI picker was too wide to share the row) | the picker shrinks to fit; a phone-width browser test checks the row |
| 65 | Reader buttons in a chat failed with a server error when the reader wasn't running or lacked a control | each control is looked up only when pressed, and refused plainly when nothing is being read |

## 15 November 2026 (6)
From the M2.2 audit (F1-F4). F3 checked: Topics come from a chat's searchable parts, which are removed when the chat
is left out, so the tab is already clean; "How it answers" holds general rules and task lessons that aren't tied to
any chat, so there is nothing to filter.

| # | Bug | Fixed by |
|---|---|---|
| 64 | Automation builder: choosing "Just run" sent an empty value, which the server read as missing, so it asked first anyway | "Just run" sends 0; a test covers it |
| 63 | Automation builder: an automation made without the ask-first field would just run (unsafe default) | missing means ask first |
| 62 | The evening revision post test failed whenever it ran after 20:00 (the planner starts a plan tomorrow that late, so it found no study day "today") | the test pins the planner's clock to midday and picks a post time that can't fall before midnight |
| 61 | A chat left out in Memory → Privacy still showed its notes in Memory search, and Prometheus (and other AI apps through MCP) still used them | one filter in the store's recall: notes, agent notes and messages from left-out chats are hidden everywhere; including the chat again brings them back |
| 60 | Skills → GitHub tab: Add and Get today's list now swapped a whole page into the body | JSON actions like the other new pages |
| 59 | System's "Waiting for you" link went to the old /approvals (an extra redirect) | links to /v3/approvals |

## 14 November 2026 (4)
| # | Bug | Fixed by |
|---|---|---|
| 58 | Clicking Prometheus on the taskbar brought it up, but clicking again didn't minimise it (frameless window) | the window is marked minimisable for Windows (no title bar added) |
| 57 | Deleting a chat always jumped to Home | stays on the list you were on; from inside that chat, goes to the chat list |
| 56 | The new menu's "Tasks" opened Exams and deadlines, not Prometheus's tasks | a real Tasks page; Exams and deadlines is under More |
| 55 | Every test run on GitHub failed since the taskbar fix (the dependency check didn't know `System` comes with pywebview on Windows) | `System` listed as provided by pywebview's Windows backend in `scripts/m0_baseline.py` |

## 13 November 2026 (5)
| # | Bug | Fixed by |
|---|---|---|
| 54 | The desktop window opened fullscreen and hid the Windows taskbar | opens maximised, stopping at the taskbar (every monitor); F11 still gives fullscreen |
| 53 | Your own message's Copy/Edit row sat under the answer, so it looked like two Copy rows | your buttons sit on the right under your message, on hover; one Copy per answer |
| 52 | Scrollbars were Windows' light classic ones on the dark screen | thin dark scrollbars |
| 51 | Copy buttons threw an error when Windows' browser refused the clipboard (found by the Windows click-through job) | one copy helper: tries the older copy way, otherwise says "Couldn't copy" |
| 50 | A calendar test started failing on 13 Nov (its sample birthday was on 12 Nov, so it had "passed") | the test uses its own fixed date |

## 12 November 2026 (7)
| # | Bug | Fixed by |
|---|---|---|
| 49 | A microphone that only opens at 44.1/48 kHz heard nothing if numpy wasn't installed (the sound was silently dropped) | the rate conversion also works without numpy |
| 48 | Instant answers with outside text (screen, phone screen, translations) showed raw `<untrusted …>` markers in the chat | markers are removed from what the owner sees |
| 47 | The uninstall script could delete the whole old `Claude-testing-` folder (it may hold other work) | it only removes Prometheus's own folder |
| 46 | "Remember…" sometimes didn't save (the AI had to decide) | remember / update memory / forget / change are done at once, without AI |
| 45 | "What did I copy this morning" used the wrong clock (failed after midnight in tests) | the clipboard agent uses the clipboard's own clock |
| 44 | Message said "the AI that plan" | grammar fixed |
| 43 | Voice could not start: "PortAudioError … MME error 1" (only one way of opening the microphone was tried) | every Windows sound system and the mic's own rate are tried; plain-words help if none works |

## 11 November 2026: on the owner's laptop and phone (23)
| # | Bug | Fixed by |
|---|---|---|
| 42 | "Answered twice in a way I couldn't use" (free Qwen on OVHcloud): its thinking or a sentence around the plan made it unreadable, and Prometheus gave up instead of trying another AI | plans are read out of thinking, sentences and code fences; after two bad answers the next AI tries (up to three) |
| 41 | "The plan is not valid JSON… (char 0)": the planning AI sent back an empty answer (its thinking used up all the room) and the same AI was asked again | more room when thinking runs out; an empty answer moves on to the next AI; a plain message if all fail |
| 40 | Laptop voice (Kokoro) wouldn't install on Python 3.14: its label says "up to 3.13" | installed with its old label ignored (it works on 3.14, tested by speaking) |
| 39 | Photo text reading wouldn't install on Python 3.14 (rapidocr-onnxruntime stops at 3.12) | switched to rapidocr 3, tested reading a photo on 3.14 |
| 38 | Phone app showed a blank screen when opened again | "Opening Prometheus" screen with Open and Change address buttons |
| 37 | Phone app closed when opened again from its icon | the app's screen no longer clears the Prometheus page |
| 36 | On the phone, the keyboard hid the Open button | the screen moves up; the keyboard's Go key opens too |
| 35 | Dates without a year ("15 Nov") would break on newer Python | a year is added while reading the date |
| 34 | Browser error shown in technical jargon | plain words: Edge or Playwright missing |
| 33 | "Add assignment maths homework…" saved the title with "Add assignment" | the phrase is dropped from the title |
| 32 | "What assignments are due this week" wasn't answered instantly | added to the instant phrases |
| 31 | "Set a timer for ten minutes" not understood (numbers as words) | spoken amounts of time become digits |
| 30 | "Voice problem" pill did nothing when clicked | opens Settings → Voice, the reason shows on hover |
| 29 | Settings squeezed into a narrow column after any Save | messages no longer sit in the page grid |
| 28 | Saving the ElevenLabs key froze the page while voice restarted | voice restarts in the background |
| 27 | Key errors shown far from the key box | shown next to the ElevenLabs box |
| 26 | Buttons stopped working after a page reload (a script crashed when run twice) | the script sets itself up once |
| 25 | A raw `{"detail":"Not Found"}` replaced the whole window | errors show as a short message instead |
| 24 | After an update the old program kept running ("Not Found", key looked unsaved) | restarts itself on new code; installer closes the old one |
| 23 | Connected apps with a quoted path wouldn't start on Windows | quotes removed from the command |
| 22 | Updates could crash on characters like "…" on Windows | Git output read as UTF-8 |
| 21 | Undoing a prompt change garbled "…" and "→" on Windows | Git output read as UTF-8 |
| 20 | Windows search showed the Python icon | shortcut carries Prometheus's app id and icon |

## 11 November 2026: earlier (19)
| # | Bug | Fixed by |
|---|---|---|
| 10–19 | Ten security issues from the second security review (laptop jobs could bypass read-only, instant phrases ran on outside text, cloud goals needed no yes, screen and phone text not marked as outside text, phone free actions too wide, screen clicks not re-checked, private messages synced in plain text, GitHub token in settings, restore paths, one backup key for all files, SMS sender not checked) | see CHANGELOG 2.2 |
| 9 | Prometheus answered its own voice | mic muted while speaking; its own words are ignored |
| 8 | When unsure what it heard, it guessed (the weather → GitHub search) | it asks again instead |
| 7 | Lecture recordings failed to decode with newer PyAV | decoded with PyAV directly |
| 6 | Message confirmations showed the number, not the name | the contact's name is shown |
| 5 | "Set an alarm on my phone" was taken as a laptop timer | phone phrases are checked first |
| 4 | The cloud-copy check crashed with older settings | it copes with missing settings |
| 3 | The login check blocked the phone app's files | those files are allowed |
| 2 | The phone notification key was rejected | added to the known keys |
| 1 | The phone layout spilled off the screen | fixed column widths on phones |

(The first security review's fixes and earlier work weren't logged one by one, so they aren't counted here.)
