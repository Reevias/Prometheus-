# Prometheus cloud copy (always on, free)

A second Prometheus on Oracle Cloud's free machine keeps reminders, email and money alerts, deadlines and the
morning and evening summaries going when your laptop is off, and answers from your phone. It has no screen, files or
voice of its own. The two copies sync every 10 minutes over a private link; while the laptop is on, the cloud copy
stays quiet so nothing arrives twice.

About 30 minutes, once.

## 1. Make the free machine (Oracle Cloud)
1. Sign up at **cloud.oracle.com** ("Start for free"). A card is asked for to check you're real; the Always Free
   machine is never charged.
2. Menu → Compute → Instances → **Create instance**.
3. Image: **Ubuntu 24.04**. Shape: **Ampere (VM.Standard.A1.Flex)**, 4 OCPUs, 24 GB memory (all inside Always Free).
4. Under "Add SSH keys", choose **Generate a key pair** and download the private key.
5. Create. When it's running, copy its **Public IP address**.

## 2. Tailscale (the private link) on your phone and laptop
1. Install **Tailscale** on your laptop (tailscale.com/download) and your phone (Play Store), and sign in with the
   same account (Google is fine). Free for personal use.

## 3. Run the setup on the machine
From your laptop (PowerShell), with the key file you downloaded:

```
ssh -i path\to\ssh-key.key ubuntu@<public IP>
```

Then on the machine:

```
curl -fsSLO https://raw.githubusercontent.com/Reevias/Prometheus-/main/scripts/cloud-setup.sh
```

(The repository is private, so if that link doesn't work, copy `scripts/cloud-setup.sh` from your Prometheus folder
with `scp -i path\to\ssh-key.key scripts\cloud-setup.sh ubuntu@<public IP>:`.)

```
bash cloud-setup.sh
```

It asks for:
- a **GitHub token** that can read your Prometheus repository (github.com/settings/tokens → Fine-grained → only that
  repository → Contents: Read-only);
- a **password** for this copy (it can be the same as your laptop's);
- a Tailscale sign-in link: open it and approve.

At the end it prints a **private address** (https://prometheus-cloud.….ts.net) and a **sync key**.

## 4. Connect your laptop
Prometheus on the laptop → Settings → **Cloud copy**: paste the address and the sync key, type your password, Save.
It syncs at once and every 10 minutes after that.

## 5. Your phone
Open the private address in your phone's browser (with Tailscale on) and sign in. The phone link step adds
notifications and an app icon.

## Keys on the cloud copy
To answer from your phone it needs an AI key: open its Settings from the phone and add your OpenRouter or Claude key.
Email and Google: connect them there the same way if you want the cloud copy to read mail while the laptop is off.

## Updating
It updates itself every night like the laptop does.
