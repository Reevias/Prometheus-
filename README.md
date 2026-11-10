# Prometheus

[![Tests](https://github.com/Reevias/Prometheus-/actions/workflows/tests.yml/badge.svg)](https://github.com/Reevias/Prometheus-/actions/workflows/tests.yml)

Your personal AI brain. It runs on your own laptop, in a browser tab. You type a task, a lead AI splits it into
steps, agents do the steps and share one memory, and you get one answer. Every file change can be undone.

Design: [`docs/spec.md`](docs/spec.md) · Roadmap: [`docs/guide.md`](docs/guide.md)

## What it can do (v1)

| Agent | Does |
|---|---|
| File Sorter | Sorts a folder into Images/Year/Month, Documents, Music, Videos, Archives, Installers, Code, Duplicates |
| Documents | Reads PDFs, Word and text files **on your laptop** and summarises the ones that match |
| Research | Searches the web and writes a summary with sources |
| Repo Scout | Finds GitHub projects for a need; clones only after you approve, and never runs them |
| Guide | Explains how to do something from one of Prometheus's skills (how-to guides) |
| Opener | Plays a YouTube video (and can summarise it from its captions), opens a GitHub repo, a website, or a file in its normal app. Never runs programs |

Try: *"sort my Downloads folder and summarise any PDFs about taxes"*.

## Install (Windows, PowerShell)

You need **Python 3.12 or newer** and **Git**. Then run this once in PowerShell:

```powershell
irm https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/install-windows.ps1 | iex
```

It downloads Prometheus to `%USERPROFILE%\Prometheus-`, installs it with the newest Python you have, asks you to
choose a password (first time only, at least 10 characters) and puts a **Prometheus** shortcut on your desktop
and in the Start menu.

## Run

Double-click the **Prometheus** shortcut. It always opens this copy, never an older one. The version is shown in
the window title.

To update: ask Prometheus to update itself (after your approval), or run:

```powershell
cd $env:USERPROFILE\Prometheus-
.venv\Scripts\python -m prometheus.cli update
```

Other commands (after `.venv\Scripts\activate`): `prometheus serve` (browser tab at http://127.0.0.1:8000),
`prometheus doctor` (checks the setup and warns if you are running an old copy), `prometheus shortcut`.

**Meaning-based memory search (optional):** `ollama pull nomic-embed-text`. Without it, memory search uses words.

## AI models

Add keys in **Settings → API keys** (paste the key, type your Prometheus password, Save). Keys stay on this
laptop, are never shown again, and work straight away.

- **OpenRouter (main brain for now):** one key, many models.
- **Free providers:** Settings lists Groq, Gemini, Mistral, NVIDIA and others, with a search box. Tick "Use it"
  to switch one on. Three need no key (Kilo Code, LLM7.io, OVHcloud) but have tight limits. Providers marked
  "may log or train on prompts" can keep what you send them.
- **Local (free, private):** start Ollama and keep `qwen3.5:2b` pulled. Personal files only ever go to local models.
- **Claude:** add an Anthropic key later. Use prepaid credit with auto-reload off.

Prometheus switches models by itself: if one is down, out of credit or rate-limited it rests for 5 minutes and
the next one takes over; when the spending cap is reached it drops to free and local models. Each answer shows
which model wrote it.

Without any model, file sorting by rules still works; planning needs a model.

## Skills

Skills are how-to guides Prometheus follows. Each is a folder with a short `SKILL.md` and a `references/` folder
it opens only when needed (see the **Skills** page). Built-in skills: research, task-planning, memory, models,
coding, understand-software, explain-code, file-sorting, video. They were written from the open-source projects
named inside each reference file.

Add your own: put a folder with a `SKILL.md` (starting with `name:` and `description:` lines between `---` marks)
in the `skills` folder of Prometheus's data folder. A skill is instructions the AI will follow, so only add ones
you trust.

## Memory

Everything Prometheus remembers is in one file (`memory.db`) shared by every agent and model. Before planning it
looks up the memories that match your request. The **Memory** page has a search box; word forms and partial
words match ("tax" finds "taxes").

## Safety

- Opens only on your laptop (`127.0.0.1`) and needs your password. Phone access on Wi-Fi is an opt-in setting.
- Never deletes: files go to the Recycle Bin, and every move can be undone on the **History** page.
- Agents can only reach your user folder; system folders, browser data, passwords, `.ssh` and Prometheus's own code
  are always blocked.
- Text from files and web pages is treated as data, never as instructions.
- Web requests can't reach your own network or computer.
- YouTube and GitHub links open straight away; any other website or any file on your laptop waits for your
  approval; programs and scripts are never opened.
- Cloning repos and self-edits wait on the **Approvals** page. The safety code itself can't be self-edited.
- **STOP ALL** at the top of every page stops every agent at once.

Your data lives in `%LOCALAPPDATA%\Prometheus` (memory, undo history, prompts, settings).

## For developers

```bash
uv venv -p 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q
```

## More

- [Changelog](CHANGELOG.md)
- [Security](SECURITY.md)
- [Contributing](CONTRIBUTING.md)
- [All decisions so far](docs/brief.md)
- [`website/`](website/): the separate sign-up/login website moved here from the old repository
- Licence: [MIT](LICENSE)
