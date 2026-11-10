# Security

Prometheus runs on your own computer and can read and move your files, so its safety rules matter.

## Reporting a problem

Please do not open a public issue for a security problem. Use GitHub's **Report a vulnerability** button on the
Security tab of this repository, or contact the owner privately. Never include real API keys or passwords.

## What Prometheus is designed to guarantee

- It listens only on your own computer (`127.0.0.1`) unless you turn on access for your Wi-Fi, and every page needs your password.
- API keys are stored only on your computer, never shown again after saving, never logged and never sent to an AI model.
- Personal file contents are only ever sent to local models.
- Files are never deleted: they go to the Recycle Bin, and every move can be undone from the History page.
- Agents cannot touch system folders, browser data, saved passwords, SSH keys or Prometheus's own data and code.
- Web requests cannot reach your own computer or home network.
- Only plain YouTube and GitHub repository pages open without asking; programs and scripts are never opened.
- Risky actions wait for your approval, and **Stop all** halts every agent.
- Prometheus cannot edit its own safety code.

## What it does not protect against

- Someone who already has your Windows login or your Prometheus password.
- Free AI providers keeping the prompts you send them (they are labelled in Settings).
- Mistakes by an AI model inside the limits above, for example sorting a file into the wrong folder (undo exists for that).

Details and the full decision history are in [`docs/brief.md`](docs/brief.md).
