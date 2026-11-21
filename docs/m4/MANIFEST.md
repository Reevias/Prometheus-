# The skill manifest (`manifest.json`)

Made by M4.4 (`prometheus/repos/skillmaker.py`), read by `prometheus/skillbook.py` (as `Skill.manifest`), checked by
M4.5 (hashes) and M4.6 (trial runs), shown by M4.3 (Candidates).

Every skill drafted from a repository has a `manifest.json` next to its `SKILL.md`. It is UTF-8 JSON, one object,
with **exactly** these eight keys. Skills without a manifest (built-in skills, older ones) work as before. A manifest
that isn't a plain file, isn't valid JSON, is over 200 KB or whose `name` differs from the folder name is ignored: the
skill then loads as if it had none.

## Fields

| Field | Type | Meaning |
|---|---|---|
| `name` | str | Same as the folder and the `name:` line in SKILL.md. `^[a-z][a-z0-9-]{0,40}$`; the slash command is `/<name>`. Drafts are named `repo-<owner>-<name>` (cut to 41 characters). |
| `version` | str | `1.0.0` style. Drafts start at `1.0.0`. |
| `description` | str | One line in plain words, written by Prometheus (never the repo's own text). |
| `source` | dict | `{"repo": "owner/name", "commit": "<40 characters>", "licence": "<SPDX id or unknown>"}`, from the repo profile (M4.1). |
| `permissions` | list[str] | Only what the scripts need, in this order: `files:read`, `files:write`, `network`; or `["none"]`. Reading the skill's own `inputs.json` needs no permission. |
| `scripts` | list[dict] | One per script: `{"file": "scripts/<x>.py", "sha256": "<64 hex>", "inputs": {sample inputs}, "timeout": seconds}`. Empty for a guide-only skill. At most 3. |
| `packages` | list[str] | Top-level Python packages the scripts import that are not in the standard library. Drafts only use packages already installed with Prometheus; a skill whose packages are missing stays off. |
| `files` | dict[str, str] | Every file in the skill folder except `manifest.json` itself (relative path with `/` → sha256 of its bytes). |

How the builder decides:
- **permissions**: read from the script's code with Python's `ast` (never run). `network` only when it imports a
  networking module (`socket`, `urllib.request`, `http.client`, `requests`, `httpx`…); `files:write` when it opens a
  file for writing or calls `write_text`, `mkdir`, `unlink`…; `files:read` when it opens a file for reading,
  `read_text`, `glob`, `listdir`…
- **licence** (`skillmaker.licence_kind`): permissive (MIT, Apache-2.0, BSD, ISC…) → scripts may be adapted, with
  attribution in SKILL.md and at the top of each script. Copyleft (GPL, AGPL, LGPL, MPL…) → guide only until the owner
  presses **Allow copying** on the Candidates card; then the scripts must stay under the same licence. Anything else
  (no licence, an uncommon one, `MIT OR GPL-3.0-only`) → guide only, no copied code.
- **scripts**: a repo file is used only if its top level is imports, functions, simple classes and constant values
  (nothing runs when it loads), it imports nothing like `subprocess`/`ctypes`/its own package, it calls nothing like
  `eval`/`exec`/`os.system`, and it has a public function whose inputs all have plain default values. Those defaults
  are the sample `inputs`. Its own `if __name__ == "__main__":` block is dropped; a runner is added that reads
  `inputs.json`, calls the function and prints the answer as JSON.

## Example 1: a permissive repo with one script

```json
{
 "name": "repo-owner-tool",
 "version": "1.0.0",
 "description": "Use ideas from the GitHub project owner/tool (Python); sandbox scripts: count_words",
 "source": {"repo": "owner/tool", "commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "licence": "MIT"},
 "permissions": ["none"],
 "scripts": [
  {"file": "scripts/wordcount.py",
   "sha256": "3f0c…(64 hex)",
   "inputs": {"text": "the cat the hat", "top": 3},
   "timeout": 60}
 ],
 "packages": [],
 "files": {
  "SKILL.md": "9a1e…(64 hex)",
  "scripts/wordcount.py": "3f0c…(64 hex)"
 }
}
```

## Example 2: a GPL repo before the owner allows copying (guide only)

```json
{
 "name": "repo-someone-gpl-tool",
 "version": "1.0.0",
 "description": "Use ideas from the GitHub project someone/gpl-tool (Python); a guide, no scripts",
 "source": {"repo": "someone/gpl-tool", "commit": "cccccccccccccccccccccccccccccccccccccccc", "licence": "GPL-3.0-only"},
 "permissions": ["none"],
 "scripts": [],
 "packages": [],
 "files": {"SKILL.md": "51b7…(64 hex)"}
}
```

## What M4.5 checks (hashes)

- The manifest has exactly the eight keys with the types above, and `name` matches the folder.
- Every path in `files` is a plain file inside the skill folder (no `..`, no links), its sha256 matches, and there
  is no other file in the folder besides `manifest.json`. Any difference: the skill is switched off with the reason.
- Every `scripts[].file` is in `files` with the same `sha256`.

## What M4.6 checks (trial runs)

- Each script runs once in `prometheus/sandbox.py` with its `inputs` written to `inputs.json`, within `timeout`.
- Network is allowed only if `permissions` lists `network` (and then the owner is asked, as the sandbox already does).
  A script that tries the network without it, fails, or times out keeps the skill off, with the reason on its
  Candidates card.
- `packages` must all be importable; if not, the skill stays off.

## Where things live

- Drafts: `<data>/skill_drafts/<name>/` (the planner never reads these).
- Accept on the Candidates card moves the draft to `<data>/skills/<name>/`, **switched off** until it passes its trial
  run (M4.6) or the owner switches it on in Skills.
