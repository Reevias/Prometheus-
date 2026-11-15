# M2 report: M2.3 and M2.4 (15 November 2026)

**In short:** M2 is finished and merged into `main`. The new look is now the only look, on the laptop and the phone.
Version stays **2.3.0** (your choice: no new version until M10). **NOT VERIFIED on your laptop or a real phone**:
everything below was tested on GitHub's Windows and Linux machines and in a browser here, not on your hardware.

## What was built

**M2.3: new basics**
- **Projects** (sidebar, after Chats): a folder, its chats, notes and your instructions together. A new project gets
  `Documents\Prometheus Projects\<name>`; or pick a folder Prometheus may use. Tabs: Files, Chats, Tasks, Agents, Notes,
  Settings. Trash keeps a project 30 days. Export and import as a .zip.
- **Pause / Carry on** for running tasks, and a **Board** (buttons on the cards, no dragging), **Timeline** and table.
- **Automation builder** (Automations → New automation): once, every day / weekday / week, or *when something
  happens* (new file in a folder, low battery, full disk, busy CPU). Each one reminds you or does a task, and you
  choose per automation whether it asks you first.

**M2.4: switch-over**
- **Phones get the new look**: the menu is a drawer (top left), no right panel, the ask box fits a phone screen,
  hold to talk, notifications from the bell.
- **System → Devices**: real health of this laptop, the cloud copy (Check now) and the phone (battery when phone
  control is on). Nothing guessed.
- **The old screens are deleted** (28 old templates, ~440 lines of old code). Every old feature has a new home first:
  `docs/m2/M2_4_SWITCHOVER.md`. Old addresses send you to the new place, so links keep working.
- **Retired:** the light theme (the new look is dark only).

## Choices I made for you (you said "pick the safest")
| Question | What I did |
|---|---|
| Where a project lives | Files in a folder; the project's records in Prometheus's database (your "merge of two") |
| Automations: ask first? | Your choice per automation; if unsure, it asks first. Risky steps always ask anyway |
| An imported project (someone sends you a .zip) | Its instructions and chats are treated as outside text until you edit the instructions yourself |
| An automation waiting for your yes, then edited or deleted | Your yes runs it as it is *now*; deleted or paused ones don't run |
| Phones | Same screens as the laptop; health of devices in System → Devices |
| Light theme | Retired with the old screens |

## Bugs
- **16 bugs fixed** in M2.3–M2.4 (bug log numbers 65–80, total now 80). 14 of them came from an **independent review
  before the merge**. The worst: Notes buttons in a chat did nothing; an imported project could give orders as if it
  were you; approving an automation could run an old version of it; a Hindi project name broke the download.
- Each fix has a test that **failed before the fix and passes after** (your rule: test, fix, test again).
- Also caught before pushing: deleting the old screens took the phone-notifications address with it; a test
  noticed and it was put back.

## Tests
- All **1259** tests pass, plus **4 browser tests**: click every control on every page, phone width,
  chat notes, hold to talk.
- Every feature tested up to M2.2 was re-run: **41 old tests** that used the old screens now check the same things on
  the new screens. No security check was dropped.
- GitHub's Windows and Linux test runs: green on every push.

## Good to know
- **Another Claude session** (on your laptop) was working on the same branch at the same time. It built Pause / Carry
  on first; I kept its version, after it passed my own tests, instead of overwriting it.
- **Not built (on purpose):** automations that act with no asking at all are M10.

## Please try on your laptop
Update (in PowerShell):

```powershell
cd $env:USERPROFILE\Prometheus-
git checkout main
git pull --ff-only
.venv\Scripts\python -m prometheus.cli serve
```

Then check: a new project, a chat inside it, Notes in a chat, an automation with "ask me first", Pause / Carry on on
a long task, and open Prometheus on your phone (menu top left, bell for notifications, hold to talk).
