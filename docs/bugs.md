# Bug log

Every bug found and fixed, newest first, so the count is always exact. Add a line for each new one.

**Total fixed: 198** (as of 25 November 2026)

## 25 November 2026, M5 open items finished (3)

| # | Bug | Fixed by |
|---|---|---|
| 198 | A removed own agent kept working in a chat until it finished | It stops at its next round |
| 197 | A cancelled or paused graph step that never checked "should I stop?" kept making AI calls and starting programs | It is stopped at its next AI call, sandbox program or terminal command |
| 196 | Steps running side by side could go over the task's spending limits together (each checked before any had spent) | Each paid call reserves its most possible cost first, and the task's limit is checked before every paid call |

## 25 November 2026, M5 audit and laptop-check risks (14)

| # | Bug | Fixed by |
|---|---|---|
| 195 | The job runner's Stop, Pause and Resume held its own lock while waiting for the database, while a graph starting a step does the opposite: the two could freeze each other | The runner's buttons use the database lock only |
| 194 | The graph runner's idle check (added earlier in this audit) could freeze with the graph's driver in the same way; GitHub's Windows run hung on it | The check never asks the database while holding the runner's lock |
| 193 | A request that ran again after an AI limit was recorded "done" in the tracker even when it failed or was stopped | Its real result is recorded |
| 192 | The jobs table only grew, and the jobs panel read all of it every 4 seconds | Finished jobs older than 30 days are tidied away at start; the panel reads only what it shows |
| 191 | When one try of a step finished, it could end the programs of the step's next try | Programs are ended per try; on Windows the Job Object is closed instead of looking up a process number |
| 190 | The code sandbox a child agent ran wasn't known to the clean-up: cancel, pause and Stop all left it running until its own time limit | The sandbox program is recorded with its child and ended with it |
| 189 | Branching a chat that is left out of memory made the copy searchable and remembered | The branch is left out too |
| 188 | After Stop all, jobs that then finished (a backup, a chat task) stayed "paused" for ever | They are recorded as they really ended (cancelled "Stopped by user"; a chat task gets Run again) |
| 187 | Pausing or stopping a chat task's step graph from the jobs panel ended the task as "done" with half the steps, and Resume could run the steps a second time | A chat task's graph has Stop only, which stops the task; a graph that ends paused or cancelled ends the task as stopped |
| 186 | The task-graph runner kept every finished graph's functions (with the task's results) in memory | They are let go when the graph ends |
| 185 | Chats sent at the same time all ran at once, outside the 2-or-3 limit (laptop check) | Chat tasks have their own limit of the same size, and wait as "Waiting for a free slot" |
| 184 | A stopped or restarted chat task had no way to run again from the jobs view (laptop check) | "Run again" button (asks first; refused while everything is stopped) |
| 183 | Stop all could leave a program running and mark a stopped step "done": programs were ended before jobs were paused, and the graph started the next step (laptop check) | Jobs are paused first, then programs ended; a graph starts nothing while everything is stopped; a program started then is ended at once |
| 182 | With under 1 GB free (normal on the 8 GB laptop), no step ever started and the task waited for ever; tests read the machine's real RAM and hung (laptop check) | Under 1 GB free steps run one at a time; tests use a fixed memory reading |

## 25 November 2026, Opus review of M5 5.4 and 5.5 (2)

| # | Bug | Fixed by |
|---|---|---|
| 181 | "Stop" on a waiting request (AI limit) in the jobs view only changed its record; it stayed in the retry queue and ran again by itself | Stop takes it out of the queue and it is never started again |
| 180 | An own agent with "read a file" and a web tool could read one of the owner's files and then send its words to the web (a page could ask it to) | After a file is read, the web tools are refused for the rest of that task |

## 24 November 2026, Opus review of M5 5.1 and 5.7 (8)

| # | Bug | Fixed by |
|---|---|---|
| 179 | An error message written by the web page reached the AI as plain text | The page's error text is wrapped as untrusted |
| 178 | Clicking by id put the button's name (chosen by the page) in the AI's message as plain text | The AI gets the id and the name wrapped as untrusted; the owner sees the name, shortened |
| 177 | The job runner kept every finished thread for as long as Prometheus ran (memory slowly grows) | Only the last 64 are kept |
| 176 | The no-untracked-threads check missed timers, thread pools, loop tasks, `import threading as _th` and extra threads in allowed files: backup, safety check, voice button and 2 push sends slipped through | The check counts every call; backup, safety check and voice button now go through the tracker; the push sends are listed with reasons |
| 175 | A waiting request that was given up stayed "paused" in the tracker (paused → failed isn't an allowed move and the error was hidden) | Ended through the allowed moves |
| 174 | A waiting request that was answered stayed "paused" in the tracker for ever | Marked done when it leaves the queue |
| 173 | Stop all and every restart marked the 13 always-on loops (reminders, sync, ...) "paused" although they kept running or start again by themselves | Loops are marked as services: Stop all leaves them, a restart closes the old record |
| 172 | A job started while Prometheus was starting (chat archive sync) was marked "paused: Prometheus restarted" by the start-up check | The start-up check runs before anything starts a job |

## 24 November 2026, M5 5.7 browser snapshot (1)

| # | Bug | Fixed by |
|---|---|---|
| 171 | After "open" and "click", the browser agent gave the page's title to the AI as plain text, so a page could put instructions in its title | The title is wrapped as untrusted data (also after sign-in and the new click-by-id) |

## 21 November 2026, Settings redesign and the owner's second set of screenshots (7)

| # | Bug | Fixed by |
|---|---|---|
| 170 | With an ElevenLabs key the local voice (Kokoro) was still loaded at start, only as a fallback, keeping hundreds of MB busy | It loads the first time the fallback is needed |
| 169 | The photo models (about 1 GB) loaded once and were never let go, so RAM stayed high after sorting photos | Released 2 minutes after their last use |
| 168 | The AI asked to run a Python script that only printed a sentence ("Conversation acknowledged…") | A script that only prints fixed text is said straight away: nothing runs, nothing is asked |
| 167 | Dragging the window to the top of the screen didn't fill it; double-clicking the title bar did nothing | Drag to the top edge = maximise, dragging a maximised window restores it, double-click = maximise/restore (NOT VERIFIED on Windows) |
| 166 | Minimise / maximise / close never worked: a press on them started pywebview's window move (the whole title bar was a drag area) | Only the bare title bar drags (NOT VERIFIED on Windows) |
| 165 | Settings search matched letters anywhere ("Ai" found every "email" and "details"), and the chats list stayed beside Settings | Words must start with what you type, best matches first; no chats list on Settings |
| 164 | The General settings form mixed spending, GitHub, Wi-Fi, updates and models; splitting it would have switched Wi-Fi access off | The save changes only the fields a form sends; each part lives in its own category |

## 21 November 2026, from the owner's laptop screenshots (3)

| # | Bug | Fixed by |
|---|---|---|
| 163 | The "GitHub skills library" key (M4.5) had nowhere to be pasted in Settings | Settings → Connections → Other keys lists it, with how to make it |
| 162 | Two identical microphone buttons next to Stop all (Voice on/off and the older Mute) | One button when voice is set up; Ctrl+Shift+M still mutes |
| 161 | "Don't allow" seemed to do nothing: the task carried on and its AI asked for the same script again, so an identical card came back | A no ends that task |

## 21 November 2026, M4 fix: your own repos (2)

| # | Bug | Fixed by |
|---|---|---|
| 160 | Reevias/Prometheus- was deleted as dangerous (its README's install line and repo_check's own malware patterns), so your main repo could never become a skill | repo_check has an `own` flag set from the repo's GitHub owner (never from its files): pattern matches give "check this first"; Defender's threat still deletes |
| 159 | Your own repos were never treated as yours (`read_repo` never got `own=True`), so the links between your repos and their NOTES.md were never made | same owner as `github_repo` → `own=True`; links and NOTES.md on the Candidates card (`prometheus/repos/own.py`) |

## 21 November 2026, M4.4 extra (1)

| # | Bug | Fixed by |
|---|---|---|
| 158 | repo_check's setup-folder rule also softened a top-level *file* named `.github` or `.devcontainer` (not a folder): its `curl … \| sh` line gave "check this first" instead of "danger" | Only files inside those folders count |

## 21 November 2026, review of M4.4–M4.7 (3)

| # | Bug | Fixed by |
|---|---|---|
| 157 | The owner's rule for repo_check (21 Nov) was never built: a `curl … \| sh` line in `.devcontainer/` or `.github/` still deleted the whole repo as dangerous (pallets/itsdangerous) | Those two top folders give "check this first"; everywhere else, and any other danger there, stays as strict |
| 156 | A skill whose scripts need the internet was switched on after Accept although none of its scripts had been run | It stays off; the owner can switch it on in Skills |
| 155 | A skill could be published to the skills library without passing its sandbox trial (or after being changed), and every device then loads library skills without a trial | Publishing needs a passed trial on exactly those files |

## 21 November 2026, review of M4.1–M4.3 and M4.8 (5)

| # | Bug | Fixed by |
|---|---|---|
| 154 | The Candidates line listed file types (reStructuredText, YAML, JSON) as if they were programming languages | Only programming languages are named |
| 153 | A repo that was found unsafe, or never downloaded and checked, could still be accepted | No Accept button for those, and the server refuses it |
| 152 | A repo could ship its own `codemap.json` or `profile.json`: symbol search trusted the fake map, and a folder with that name crashed the save | Those names at the top of a repo are refused |
| 151 | Folders inside a repo's zip didn't count towards the 20 000 limit, so a zip of millions of empty folders was unpacked | Folders count too |
| 150 | One file that couldn't be saved (a file and a folder with the same name; on Windows a path that is too long) stopped the whole download with a raw error | That file is listed as refused and the rest is read |

## 20 November 2026, fewer pages (5)

| # | Bug | Fixed by |
|---|---|---|
| 149 | The voice check crashed every page in the first minute after the computer started | Always checks the first time |
| 148 | With the side panel closed the chat stayed a narrow column | It grows to about 1200 px |
| 147 | Settings was one long page: scrolling the Voice settings ran into every other setting | Tabs that show only their own settings; cards laid out as rows |
| 146 | Ten pages plus Approvals and Connections: too many places to look | Four pages (Home, Tasks, Abilities, Study & Money); the rest share tab bars or live in Settings |
| 145 | The mic button's colours didn't say whether it was listening | Green when on, red when off or stopped |

## 20 November 2026, navigation (2)

| # | Bug | Fixed by |
|---|---|---|
| 144 | No quick way to switch the microphone off | A mic on/off button next to Stop all |
| 143 | Opening a chat in a project took 4 clicks; the sidebar showed only page links | Sidebar with Projects and Recents (1-2 clicks); pages under More |

## 20 November 2026, the owner's second laptop check (5)

| # | Bug | Fixed by |
|---|---|---|
| 142 | Minimise, maximise and fullscreen did nothing when Prometheus also ran in the background; the window couldn't be resized | The buttons talk to the window directly; edges resize it |
| 141 | "Waiting for your approval" gave no way to find the approval | It names the kind and opens it |
| 140 | Renaming a chat could bring back a copy under the old name | Chats have a permanent id |
| 139 | Deleted chats came back after syncing with the cloud copy | Deletions travel with the sync |
| 138 | Your name was forgotten | Settings changed in place; the name box is the page's own |

## 20 November 2026, the owner's first laptop test of M3 (11)

| # | Bug | Fixed by |
|---|---|---|
| 137 | Answers sounded stiff and robotic | Writing rules ask for a friendly, human voice (old rules updated on laptops) |
| 136 | A plain "hey" took 7 seconds (sent to an AI) | Greetings get an instant friendly reply, no AI |
| 135 | Two Prometheus windows (one with the Python icon) | One window at a time; a second launch brings it forward |
| 134 | Stop all, voice off and Quit didn't stop the microphone | Stop all and Quit switch it off; voice can't start while stopped |
| 133 | Every step's whole output pasted in when no AI could write the answer; long answers couldn't be shrunk; the browser bar never went away; "Already approved." on a double click | Short fallback, Show more / less, bar only while in use with Close, no extra message |
| 132 | "How much did I spend on food last month" gave this month's total | Spending by period and category |
| 131 | A chart a script made didn't show in the chat | Pictures a task saved show under its answer |
| 130 | "Run python to make a bar chart" gave the code as text | One job with several action words runs a plan; the quick AI can't skip tools |
| 129 | Video summaries opened browser tabs, used random videos or found none | YouTube's own search as fallback, links in a list used, no tabs unless asked, failed summary said |
| 128 | A script failed on `$step3.summary` pasted into its code | Results reach scripts as inputs.json, never as code |
| 127 | A note was saved with "$step6.best_summary" in it | Step results inside a text are filled in; a missing one stops the step |

## 20 November 2026, M3 exit check (D6) (4)

| # | Bug | Fixed by |
|---|---|---|
| 126 | The login page was still the old 2.x screen (M2 said the old screens were gone) | Login in the v3 look with the orbital sphere; old `style.css` and `orb.js` deleted |
| 125 | An older update request stayed on the Approvals page next to a newer one and could only fail | Newer commits close the older request |
| 124 | Scripts in the Windows venv sandbox couldn't use packages that need a `.pth` file (pywin32...) | The guard reads the venv's `.pth` files after its rules are on |
| 123 | An approved update that added a package only pulled the code, so the automatic restart could start without it | After the yes it installs the packages; if that fails the old code goes back and nothing restarts |

## 18 November 2026, M3 audit fixes, Phase A (6)

| # | Bug | Fixed by |
|---|---|---|
| 122 | An approval could run after Stop all | Hold the request until Resume |
| 121 | Chat updated without approval and automatic updates pulled main and ran pip | Review and fast-forward the exact commit on the current branch, with approval for dependency or top-level additions |
| 120 | Billed catalog providers were treated as free | Per-provider billing switch, approval and model-card cost accounting |
| 119 | Mixed-case and escaped outside-text markers missed command checks | Shared marker check across routing and shortcuts |
| 118 | Spending, study and personal totals could receive a guessed answer | Route them to the planner |
| 117 | The Windows virtual-environment launcher exceeded the sandbox process limit | Run the base interpreter with the environment's package directories |

## 17 November 2026, M3 audit fixes, Phase B (8)
From `docs/m3/M3_AUDIT_FIX_PLAN.md`. Each fix has a test that failed before it (`tests/test_v3_m3_audit_b.py`).

| # | Bug | Fixed by |
|---|---|---|
| 116 | A job sent from the cloud copy that could change things ran on the laptop without a yes | it waits for a one-tap approval; reading jobs run as before |
| 115 | A long job could use up the Antigravity cap twice (cost was checked only at start) | each job holds back its worst-case cost while it runs |
| 114 | The 10-file Recycle limit was per step, so a task could recycle files in steps of 10 | the limit counts the whole task |
| 113 | Copying or moving a .bat/.ps1 (or other runnable file) needed no yes | it asks first |
| 112 | Terminal output didn't hide MCP, webhook, sync or tool-server tokens | every saved secret is hidden |
| 111 | The webhook token was in the URL and in the access log | it can ride in a header; the log shows /hooks/*** |
| 110 | AIs that need no key were on for new installs and received memories | off by default; they never get memories or notes |
| 109 | The step-by-step loop called a task "done" while a yes was waiting, and Pause didn't keep its place | it keeps its place and carries on after the yes or Carry on |

## 16 November 2026, M3 review (13)
An independent review of M3. Each fix has a test that failed before it (`tests/test_v3_m3_review.py`).

| # | Bug | Fixed by |
|---|---|---|
| 108 | The front door answered questions that need a tool ("any new emails?", "who won the match?", "how much disk space?") without the tool | such questions always get the normal plan |
| 107 | Stop during a quick answer still posted the answer | Stop is checked when it arrives |
| 106 | A provider-wide model card could stand in for an expensive model's own card | the exact model's card wins |
| 105 | An oddly tagged step could send a non-picture job to a picture-making AI | odd tags ignored; picture AIs filtered from every list |
| 104 | An error inside a streamed answer was saved as if the answer were complete | it counts as a failure and the next AI answers |
| 103 | A streamed OpenRouter call with no words wasn't counted in spending | both calls counted |
| 102 | One big paid call could take a task past its $0.10 limit | the call's most possible cost is checked first |
| 101 | The front door's quick check could use the hand-picked (paid) lead AI | free AI only, never the lead AI |
| 100 | Model cards put a stronger paid AI before free ones on light jobs, and before your own Settings picks | free first for light jobs; your picks stay first |
| 99 | On the cloud copy, a long job could run laptop tools on the server instead of waiting for the laptop | no step-by-step loop on the cloud copy |
| 98 | Waiting for the phone was announced as "waiting for your yes" | it isn't any more |
| 97 | A phone job that kept failing re-ran every 2 minutes for ever; a busy chat's job was dropped | at most 3 tries; busy chats keep it |
| 96 | A crash in the step-by-step loop left the task "running" for ever | the task ends with a plain message |

## 16 November 2026, security audit (15)
Five reviewers read the whole app against the Claude-BugHunter vulnerability classes (files and paths, XSS/CSRF/login,
commands and web requests, SQL and secrets, prompt injection and approvals). Each fix has a test that failed first.
Report: `docs/reports/SECURITY_AUDIT_16NOV.md`.

| # | Bug | Fixed by |
|---|---|---|
| 95 | Chat export crashed (server error) for a chat with a Hindi or other non-Latin title | one safe download-name helper for every export |
| 94 | A page's text containing </UNTRUSTED> in capitals could close the outside-text wrapper early | the wrapper is case-blind |
| 93 | The browser password approval didn't say which site gets the password, and didn't re-check it | it names the real page and refuses if the page changed |
| 92 | An MCP app's request showed only the first 300 characters on Approvals, but ran up to 2000 | the whole request is shown |
| 91 | Scripts and own tools given your files weren't treated as private, so what they printed could reach a cloud AI | private when given your files |
| 90 | Quiz and exam answers made from private notes were stored as not private and sent to the cloud planner later | stored private |
| 89 | Translate fell back to a cloud AI for text taken from your private files when the laptop AI was down | never for text from your files (typed words still may) |
| 88 | An error reply naming an Obsidian note wasn't escaped | escaped, plain text |
| 87 | Login cookies had no Secure flag on the https cloud copy | Secure when the proxy says https |
| 86 | On the cloud copy, 5 wrong passwords from any one device locked everyone out | attempts counted per device |
| 85 | The MCP web-address check was a prefix match (http://localhost.evil.com passed) | the host is compared exactly |
| 84 | The script approval showed only the first 1500 characters, so a script could hide its second half | the whole script is shown |
| 83 | A script could run any command through ctypes (libc system/popen/exec) and slip past every sandbox rule | dangerous system calls refused |
| 82 | Renaming a file accepted names Windows reads differently (a.exe. , a.txt:stream, reserved names) and skipped the safety checks | such names refused; the new name gets the same checks |
| 81 | Copying a folder (e.g. your home) copied protected files inside it (.ssh keys, Prometheus's own keys) out | protected places are skipped |

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
