# jcode: setup and providers

Source: https://github.com/1jehuang/jcode (MIT, as of 2026-11-09)
Files read: README.md, docs/WINDOWS.md, OAUTH.md, docs/WRAPPERS.md, TELEMETRY.md, src/cli/args.rs, src/cli/provider_init.rs, crates/jcode-provider-metadata/src/lib.rs.

## What it is

jcode is a terminal coding agent written in Rust. The README stresses low memory use per session and the ability to run many agents at once. The 8 GB laptop is not a problem for jcode itself; the model is the heavy part.

## Install on Windows 11

The repo says Windows 11 x64 is supported and manually checked (docs/WINDOWS.md). It needs PowerShell 5.1 or newer.

1. Get the owner's approval first. Then open PowerShell and run the documented line: `irm https://jcode.sh/install.ps1 | iex`
2. The installer picks x64 or ARM64, downloads the release from GitHub, checks it against the release's `SHA256SUMS`, installs under `%LOCALAPPDATA%\jcode`, and adds `%LOCALAPPDATA%\jcode\bin` to the user PATH.
3. Open a new terminal and run `jcode --version`.
4. To check the file yourself: `Get-FileHash (Get-Command jcode).Source -Algorithm SHA256` and compare with `SHA256SUMS` on the GitHub release page.
5. User data and config live in `%USERPROFILE%\.jcode`.

Optional extras (Alacritty terminal, global hotkey) are off by default and need explicit flags. A source build (`-BuildFromSource`) needs Git, Rust and Visual Studio 2022 Build Tools; not needed for normal use.

Windows warnings: the doc says signing is prepared but not yet confirmed active. A SmartScreen "Windows protected your PC" notice may appear. The doc says do not tell users to disable Defender or add exclusions; verify the URL and checksum instead.

## Choosing a provider

Use `--provider <name>` (or `-p`) and `--model <id>` (or `-m`). List choices with `jcode provider list` and `jcode model list`. Show the current choice with `jcode provider current`.

| Option | Needs | Fit for the owner |
|---|---|---|
| `openrouter` | OpenRouter API key (`OPENROUTER_API_KEY` or `jcode login openrouter`) | Good. Pay per use; code goes to the cloud. |
| `ollama` | Local Ollama at `http://localhost:11434/v1`; no key | Good for private files; slow on CPU. A 2B model is weak at coding. |
| `anthropic-api` (alias `claude-api`) | `ANTHROPIC_API_KEY` or `jcode login --provider anthropic-api` | Works; this is the API-key route. |
| `claude` | Claude subscription OAuth login | Do NOT use. See below. |
| `openai-compatible` | base URL + key | Any other server |

Custom endpoint, as documented in OAUTH.md: `jcode provider add <name> --base-url <url> --model <id> --api-key-stdin --set-default`, then `jcode --provider-profile <name> auth-test --no-tool-smoke`. For a localhost server use `--no-api-key`. Profiles are stored in `~/.jcode/config.toml`; keys go in a private env file in jcode's config folder.

## The Claude subscription warning

OAUTH.md describes `jcode login --provider claude`, which signs in with a Claude subscription and sends requests that copy Claude Code's identity (headers and a system-prompt line). The owner's rule is that a subscription is only for the official Claude Code app, so Prometheus must not guide the owner to use `--provider claude` with a subscription. Use an Anthropic API key instead, or OpenRouter.

## Safe first run

1. `jcode login --provider openrouter` (key is typed into the login prompt, never into chat).
2. `jcode --provider openrouter run "Reply with exactly OK"`.
3. For scripts, docs/WRAPPERS.md advises `jcode --quiet --no-update --no-selfdev ...`. `run --json` returns machine-readable output.

## Privacy

- Anything the agent reads is sent to the chosen provider. Use Ollama for personal files.
- TELEMETRY.md: usage statistics are collected; `JCODE_NO_TELEMETRY` or `DO_NOT_TRACK` stops uploads, and `/telemetry` in the app changes settings.
- Credentials: jcode may offer to read other tools' login files and asks first; deny that.

## Not verified

- Nothing was run; install steps are as documented.
- Whether Ollama's `qwen3.5:2b` handles jcode's tool calls is untested.
- Exact first-run prompts on Windows.
- Whether the hosted installer matches this repo's script.
