# Changelog

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
