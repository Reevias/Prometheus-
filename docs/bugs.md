# Bug log

Every bug found and fixed, newest first, so the count is always exact. Add a line for each new one.

**Total fixed: 40** (as of 11 November 2026)

## 11 November 2026: on the owner's laptop and phone (21)
| # | Bug | Fixed by |
|---|---|---|
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
