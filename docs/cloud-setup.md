# Prometheus in the cloud (free, no card): Cloudroom on Render

Updated 26 November 2026. About 20 minutes, once.

Two different things used to live on one "free cloud machine". Please keep them apart:

1. **Cloudroom** is a small free machine that can run **one task at a time** for you when you say yes. The steps below
   set this up on **Render**, which needs **no card**.
2. **The always-on cloud copy** (reminders, email and money alerts and phone answers while your laptop is off). **The
   Render files do not run this yet.** See "What this does not do yet" at the end. Please read it before you count on
   reminders while the laptop is off.

Oracle Cloud was dropped: it needs a card, and yours didn't work.

## What Render's free machine is really like (read this first)
- **It sleeps after 15 minutes** without a visitor and takes about a minute to wake. A free "uptime checker" (step 6)
  visits it **every 10 minutes** to keep it awake.
- **750 free hours a month** for the whole Render account. One always-awake service uses about 744 of them, so only
  **one** service can stay awake all month.
- **Its files are wiped** every time it restarts, sleeps, or you redeploy. **Nothing is kept only there**: a moved task
  keeps its progress as receipts on your laptop, and your knowledge stays on the laptop and, sealed, on Google Drive.
- **Small: 512 MB of memory.** A moved task gets at most **256 MB, 300 seconds, 5 MB of files and no internet.** If a
  task doesn't fit, Prometheus says so and keeps it on the laptop.
- It can restart at any time, has no SSH, and Render itself says free services are not for important work. Free
  services that send a lot of data can be suspended. It is fine for a personal helper like this one.
- Every task needs your yes first, and every request is signed with your own key: a request without it is refused.

## 1. A Render account (no card)
Go to **render.com** and sign up with your GitHub account. No card is asked for the free plan.

## 2. Make your Cloudroom key
On your laptop (PowerShell), make a long random key and keep it for step 3 and step 7:

```
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Don't share it and don't put it in a file in the repository.

## 3. Put the service on Render
1. In Render: **New → Blueprint**, connect your repository **Reevias/Prometheus-** (Render asks to allow its GitHub
   app for that one repository; it is private, so this is needed).
2. Pick the branch, and set **Blueprint file path** to `cloud/render/render.yaml`.
3. Render shows one service, `prometheus-cloudroom`, on the **free** plan, and asks for **CLOUDROOM_TOKEN**: paste the
   key from step 2.
4. Apply. The first build takes a few minutes. It only copies the `prometheus` code folder into a small Python image
   (it never gets your data or other keys) and starts the Cloudroom server.

When it says **Live**, copy the address (like `https://prometheus-cloudroom.onrender.com`). Open
`<address>/v1/health`: you should see `{"ok":true}`. Anything else on the service is refused without your key.

(Render doesn't update it by itself: `autoDeploy` is off, so a new version goes up only when you press Deploy.)

## 4. Check it
- `<address>/v1/health` answers `{"ok":true}` (no key needed, this is what the uptime checker uses).
- Anything else, for example opening `<address>/v1/tasks` in a browser, is refused.

## 5. Private link for your phone (Tailscale)
This is what lets your phone control the laptop (Settings → Features → "Control the laptop from the phone"), and it is
free for personal use.
1. Install **Tailscale** on your laptop (tailscale.com/download) and on your phone, signed in to the same account.
2. On the laptop, turn on "Access from other devices" in Settings, so the phone can reach it by its Tailscale address.
3. Phone control only answers over this private link and only to a phone you paired on the laptop (System → Devices).

## 6. The free uptime checker (keeps it awake)
Use any free uptime or "cron" web service that can visit an address **every 10 minutes** (for example UptimeRobot or
cron-job.org; their free limits change, so check the current ones). Add one monitor that visits
`<address>/v1/health`. It gets only `{"ok":true}`: it has no key and sees none of your data.

Without it, the service sleeps after 15 minutes, and the first request wakes it in about a minute. That is fine for a
moved task (Prometheus waits), but nothing on Render can ring a reminder while it sleeps.

## 7. Tell Prometheus
The key from step 2 goes in Prometheus as **Cloudroom key** (Settings → Connections → Keys). **NOT VERIFIED / not built
yet:** the screen where you type the Render *address*, and moving a task from the chat, aren't wired into the app in this
step; the laptop side (`CloudroomRuntime`) exists and is tested against a pretend server only.

## What this does not do yet (please read)
- **The cloud copy is not part of these files.** The old always-on cloud copy (reminders, email and money alerts,
  morning and evening summaries and phone answers while the laptop is off; `scripts/cloud-setup.sh`) is a full
  Prometheus. These files run **only the Cloudroom server**, for three reasons: Render's free service has **one web
  port** (the cloud copy and Cloudroom would both need it), **512 MB** is tight for both, and the free machine **forgets
  all its files on every restart**, so a cloud copy there would lose its password, its sync key and its AI keys each time.
  So **reminders still do not ring while the laptop is off**, and the old way of running a cloud copy needs a machine
  that keeps its files. How to host it (or a smaller version of it) is an open decision for the next review; I did not
  guess.
- If you already have another always-on Linux machine that keeps its files, `scripts/cloud-setup.sh` still sets the
  cloud copy up there: it asks for a GitHub token, a password and a Tailscale sign-in, then prints an address and a
  sync key for Settings → Cloud copy. Its header still mentions Oracle; the steps don't depend on it.
- **NOT VERIFIED:** the Docker build and the real Render deploy have not been run (no Docker engine and no Render account
  were available). What was tested: the start command in a clean environment with only `requirements.txt`, answering
  `/v1/health` and refusing an unsigned request. Please try it once and tell me what Render says.
