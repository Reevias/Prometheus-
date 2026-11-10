# Prometheus

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

Try: *"sort my Downloads folder and summarise any PDFs about taxes"*.

## Install (Windows, PowerShell)

You need **Python 3.12** (`py -3.12 --version`) and **Git**.

```powershell
git clone https://github.com/Reevias/Prometheus-.git
cd Prometheus-
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -e .
prometheus init
```

`prometheus init` asks you to choose a password (at least 10 characters).

## Run

```powershell
cd Prometheus-
.venv\Scripts\activate
prometheus serve
```

Open **http://127.0.0.1:8000** and log in. Stop it with **Ctrl+C**.

Check your setup any time:

```powershell
prometheus doctor
```

## AI models

- **Local (free, private):** start Ollama and keep `qwen3.5:2b` pulled. Personal files only ever go to local models.
- **Claude (later):** set your key, then restart Prometheus:
  `setx ANTHROPIC_API_KEY "your-key"` (close and reopen PowerShell). Use prepaid credit with auto-reload off,
  and set a spending cap in **Settings**.
- **Gemini 4 Argon:** coming in v1.1.

Without any model, file sorting by rules still works; planning needs Ollama or a key.

## Safety

- Opens only on your laptop (`127.0.0.1`) and needs your password. Phone access on Wi-Fi is an opt-in setting.
- Never deletes: files go to the Recycle Bin, and every move can be undone on the **History** page.
- Agents can only reach your user folder; system folders, browser data, passwords, `.ssh` and Prometheus's own code
  are always blocked.
- Text from files and web pages is treated as data, never as instructions.
- Web requests can't reach your own network or computer.
- Cloning repos and self-edits wait on the **Approvals** page. The safety code itself can't be self-edited.
- **STOP ALL** at the top of every page stops every agent at once.

Your data lives in `%LOCALAPPDATA%\Prometheus` (memory, undo history, prompts, settings).

## For developers

```bash
uv venv -p 3.12 .venv && uv pip install -e ".[dev]"
.venv/bin/pytest -q
```
