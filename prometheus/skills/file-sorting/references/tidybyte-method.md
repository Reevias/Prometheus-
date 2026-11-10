# TidyByte: the method

Source: https://github.com/Krinal24/folders-organizer (NO LICENCE, as of 2026-10-24). Described in my own words; no code is copied and none should be reused.
Files read: README.md, backend/app/planner.py, backend/app/rules/rules.py, backend/app/ai/prompts.py, backend/app/ai/batched.py, backend/app/ai/analyzer.py, backend/app/content/extract.py, backend/app/scanner/scanner.py (parts), backend/app/executor/executor.py.

## Idea

An organiser for messy folders such as Downloads. It suggests, the person reviews, and only then does plain code apply the changes. The README says the AI only ever suggests; moving and deleting is done by ordinary code that checks each step itself.

## Pipeline

| Step | What happens |
|---|---|
| 1 Scan | Lists files and sizes. Folders that look like code projects (markers such as `.git`, `package.json`, `pyproject.toml`) stay together as one item. Skips links, junctions, Windows system-attribute files and noise files like `desktop.ini`. Caps at 50,000 items. Flags OneDrive online-only files so they are not opened (that would download them). |
| 2 Rules | Instant, no AI. See below. At most one suggestion per path. |
| 3 AI | Documents and images only, in batches (default 40 per request, `AI_BATCH_SIZE`). Results stream to the screen. |
| 4 Review | The person changes any suggestion: move elsewhere, keep, or delete. |
| 5 Apply | Each operation is checked again against the disk, then done. |

## The rules

- Exact duplicates: group by size, then a hash of the first 64 KB, then a full SHA-256. The original is chosen by a sort key and copies are suggested for removal with high confidence. Names like `report (1).pdf` or `- Copy` act as hints.
- Partial downloads (`.crdownload`, `.part`, `.tmp`, `~$` files) older than one day.
- Empty files, and empty folders.
- Installers (`.exe`, `.msi`, `.dmg`, and similar) older than 30 days.
- By type: videos, music, archives, installers and code projects go to fixed folders.

## What the AI is shown

Only these fields: name, relative path, kind, extension, category, size, file count. No timestamps or local flags. Documents and images get separate short prompts, each with a small list of folders (for example Documents/Study, Finance, Work; Images/Screenshots, Photos, Graphics), because a small model chooses better from few options. Design details worth noting:

- The catch-all folder is listed first, and a throwaway "decoy" item is sent first, because small models lean towards the first option.
- Reply format is one line per item: number, label, confidence 1 to 9, reason of five words.
- Confidence below 4, an unknown label, or no usable answer becomes "review" for the person.
- Documents with unclear names (like `DOC-2022...pdf`) get the first ~200 characters of their text, read locally (PDF first 2 pages, DOCX, PPTX, XLSX, TXT). Setting `AI_READ_CONTENTS`: `unclear` (default), `all`, `off`. If neither name nor text says anything, it does not ask the AI; the file stays where it is or goes to Documents/Other.
- Existing top-level folders (up to 15) are offered as destinations.
- Providers: Ollama (default model `gemma3:4b`) or Hugging Face.

## Apply and undo journal

- Re-check root, symlinks, inside-folder test, and "changed since scan" (modified time and size).
- Never overwrites: a taken name gets `(1)`.
- Deletes go to the Recycle Bin; order is delete files, then moves, then remove folders.
- Destinations must be relative and inside the folder (no drive, no `..`).
- A journal file per run (in `%LOCALAPPDATA%\TidyByte\journals`, outside the user's folder) is saved after every change by writing a temp file then renaming, so a crash still leaves a usable record. Undo moves files back and removes folders the run created if empty. Deleted files are not restored automatically; they sit in the Recycle Bin.

## Lessons for Prometheus

Rules first, model second; show the plan before acting; re-check before each change; keep a crash-safe journal; keep the model's job tiny and its answers validated.

## Not verified

- Nothing was run; the frontend was not read.
- Exact category extension lists and the sort key for choosing the "original".
- Hugging Face behaviour.
