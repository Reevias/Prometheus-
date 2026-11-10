# Prometheus File Sorter: what it does today

Source: Prometheus's own code (this project, not a public repo). Files read: prometheus/agents/file_sorter.py, prometheus/actions/files.py, prometheus/actions/journal.py, prometheus/safety/paths.py. Licence: owner's own code; for the idea source see tidybyte-method.md.

## Inputs

- `folder`: default `Downloads`; resolved by the agent's `resolve_folder` helper (not read here).
- `layout`: `auto` (default), `type`, or `date`.

## Steps

1. The folder must pass the path policy (see blocked folders). If not, the run fails with the reason.
2. It lists only files directly inside the folder. Subfolders are not entered or moved.
3. Skipped: symbolic links, files whose names start with `.`, `desktop.ini`, `thumbs.db`, `.ds_store`, `ehthumbs.db`, and files with the Windows Hidden or System attribute.
4. It records each file's modified time and size.
5. Duplicates: every file gets a SHA-256 hash; files with identical content form a group. The newest-modified one stays; the others are moved to `Duplicates`.
6. Each other file gets a destination (below), is moved, and a line is added to the audit log.
7. The summary says how many moved, how many skipped (with up to five reasons), and how many PDFs were found; PDF paths are passed on to later steps.

## Categories (by extension)

Images, Videos, Music, Documents (pdf, doc, docx, txt, md, rtf, odt, xls, xlsx, csv, ppt, pptx), Archives, Installers, Code, and Other for everything else.

## Layouts

| Layout | Result |
|---|---|
| `auto` | Images go to `Images/YYYY/MM` using the photo's taken date; everything else by category |
| `type` | Category folder only (images in `Images`) |
| `date` | `YYYY/MM` from each file's modified time, any type |

Photo date: EXIF "date taken" if the picture has it, otherwise the file's modified time.

## The unclear-name rule

Only in `auto` or `type` layout, only for Documents, and only when the name stem looks like a hash or scan: starts with six or more hex characters, or `doc`, `scan` or `img` followed by digits. For those the local model gets the file name and the first 200 characters of text (wrapped as untrusted data) and answers with a one-word folder under `Documents`. The call uses the "private" route (local model). The answer is cleaned: only one safe name part, letters, digits, spaces and a few symbols, up to 61 characters; anything else becomes plain `Documents`. If the model is unavailable, the file goes to `Documents`.

## Safety around every move

- Path policy: both the folder and each file must be inside the home folder and under the task folder; links are refused.
- Blocked folders: `AppData`, `.ssh`, `.gnupg`, `.aws`, browser profile folders for Chrome, Chromium and Firefox, keyring and password-store folders, names starting with `keepass`, Prometheus's own code folder, and its data folder's keys and settings files.
- Before moving, the file's modified time and size must match the earlier scan; otherwise it is skipped with "Changed since the scan".
- Never overwrites: a taken name becomes `name (1).ext`. No delete in this agent. A kill switch is checked between files.
- Moves that fail (file in use) are skipped with a reason.

## Undo

Each run gets an id like `20261110-101500-ab12cd34`. A JSON journal file (one per run) is written after every change, using write-then-rename. It stores the root, each move (from, to, relative paths) and folders created. `undo_run` walks entries in reverse, moves files back, and removes folders the run created if they are empty. It refuses while the run is still active. If something else is at the old path, or the file moved again, that entry is reported as a problem and the rest still undo; the run is marked undone only when every move is back, so the owner can retry. Single entries can be undone too. Recycled files (not produced by this agent) would have to be restored from the Recycle Bin.

## Not verified

- Nothing was run.
- How `resolve_folder` maps names like `Downloads`.
- That the "private" route always means local Ollama.
- Behaviour of Windows-only attributes on other systems.
