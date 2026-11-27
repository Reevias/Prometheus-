# Your M6 setup and check: step by step (27 November 2026)

Do the parts in order. Each step says exactly where to click. Parts B and C are needed for Cloudroom; the rest are
checks of things that are built but not yet tried on your laptop or phone. Write down what happens at each
**Check** line (a screenshot is fine), so the next session can fix anything that goes wrong.

Time: Part B about 20 minutes; the rest about 5–10 minutes each.

---

## Part A — Get the newest Prometheus on the laptop

1. Close Prometheus (tray icon by the clock → Quit, or close the window).
2. Open **PowerShell** (Start → type `PowerShell` → Enter).
3. Copy and run these two lines:
   ```powershell
   cd $env:USERPROFILE\Prometheus-
   .venv\Scripts\python -m prometheus.cli update
   ```
4. Start Prometheus again with the **Prometheus** shortcut.

**Check:** Settings → Connections shows a section called **Cloudroom (Render)** after you press
**Show advanced settings**. If it isn't there, the update didn't work: send a screenshot of the PowerShell window.

---

## Part B — Cloudroom on Render (free, no card)

### B1. Make your Cloudroom key
1. In PowerShell (still in the `Prometheus-` folder), run:
   ```powershell
   .venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```
2. It prints one long line of letters and numbers. **This is your Cloudroom key.** Copy it into Notepad for now.
   Don't send it to anyone, don't put it in a chat, and don't save it in the Prometheus folder.

### B2. Make a Render account
1. Open **https://render.com** and press **Get Started** (or **Sign Up**).
2. Choose **GitHub** and sign in with your GitHub account (the one that owns `Reevias/Prometheus-`).
3. If Render asks for a plan, choose **Hobby (free)**. **No card is needed**; if it asks for a card, stop and tell me.

### B3. Create the Cloudroom service
1. In the Render dashboard press **New +** (top right) → **Blueprint**.
2. Under **Connect a repository**, press **Configure account** / **Connect GitHub** if your repository isn't listed.
   On GitHub's page choose **Only select repositories** → pick **Reevias/Prometheus-** → **Install**. (The repository is
   private, so Render needs this permission to read it.)
3. Back in Render, pick **Reevias/Prometheus-**.
4. **Branch:** `main`. **Blueprint Path:** `cloud/render/render.yaml`.
5. Give the Blueprint a name, for example `prometheus`, and press **Apply** (or **Deploy Blueprint**).
6. Render creates one service called **prometheus-cloudroom** on the **Free** plan and starts building it.
   **The first start will fail — that's expected**, because it doesn't have your key yet. Go on to B4.

### B4. Give Render your key as a Secret File
1. In the dashboard open the service **prometheus-cloudroom**.
2. In the left menu press **Environment**.
3. Scroll to **Secret Files** and press **+ Add Secret File**.
4. **Filename:** `CLOUDROOM_TOKEN` (exactly like this, capital letters, no extension).
   **Contents:** paste your Cloudroom key from B1 (only the key, nothing else).
5. Press **Save Changes**.
6. At the top press **Manual Deploy** → **Deploy latest commit**. Wait until the status says **Live** (a few minutes).

**Check:** copy the address shown at the top of the service page (it looks like
`https://prometheus-cloudroom.onrender.com`, maybe with extra letters). Open
`<your address>/v1/health` in your browser. You should see exactly `{"ok":true}`.
Then open `<your address>/v1/tasks`: it must **not** work (an error is correct — it needs your key).

### B5. Tell Prometheus where Cloudroom is
1. In Prometheus open **Settings** → **Connections** → press **Show advanced settings** → **Cloudroom (Render)**.
2. **Address:** your Render address from B4 (starts with `https://`, nothing after `.com`).
3. **Cloudroom key:** paste the same key from B1.
4. **Your Prometheus password:** your normal Prometheus password.
5. Press **Save**.

**Check:** it says **"Cloudroom is connected."** If it says the key is missing, paste the key again.
Now delete the key from Notepad (it's saved in Prometheus and in Render).

---

## Part C — Keep Cloudroom awake (optional, free)

Without this, Render sleeps after 15 minutes and the first task waits about a minute while it wakes.

1. Open **https://cron-job.org** (or **https://uptimerobot.com**) and make a free account. (Their free limits change;
   if one asks for a card, use the other.)
2. Create a new job / monitor:
   - **URL:** `<your address>/v1/health`
   - **Every:** 10 minutes
   - Method **GET**, no password or key (it only ever gets `{"ok":true}`).
3. Save and switch it on.

**Check:** after 20 minutes the job's history shows successful visits. On Render's free plan this uses most of the
month's 750 free hours on this one service, which is fine.

---

## Part D — Your first Cloudroom task

1. In a Prometheus chat type: **work out the sum of the numbers from 1 to 1000 in Cloudroom**
2. Prometheus answers that moving the script needs your yes. Open **Approvals** (the waiting badge, or System →
   "Waiting for you").
3. Read the question: it shows **all** the code that goes up, any files, and the limits (no internet, 300 seconds,
   256 MB). Press **Approve**.
4. Wait up to a minute (longer if Render was asleep).

**Check:** the chat gets a message **"Cloudroom finished …"** with the answer **500500**.
**Also check Stop:** ask for a slow one — **in Cloudroom, run a script that waits 2 minutes and then prints done** —
approve it, open the **Agents** page (jobs list), press **Stop** on it. It should show cancelled within a few seconds.

---

## Part E — Cloudroom storage on Google Drive (optional)

Needs Google Drive connected and backups set up (you have a recovery code).

1. **Settings → Features** → switch on **Cloudroom storage**.
2. Wait for the next night, or press **Backup now** in Settings → Privacy and safety → Backup.

**Check:** in Google Drive there is a folder **Prometheus Cloudroom** with files whose names are long letter codes
(not your file names — that's on purpose). Opening one shows nothing readable.

---

## Part F — Prometheus using your screen, mouse and keyboard

1. Open **Notepad** and leave it on screen.
2. In Prometheus type: **look at my screen and type hello into Notepad**
3. The first time, it asks once to read the screen. Then it asks for the typing, showing the exact text and the window.
   Approve both.

**Checks:**
- "hello" appears in Notepad, and only after your yes.
- Ask it to **type my password into the password box** on any login page: it must **refuse**.
- While it is waiting for a yes, press **STOP ALL** (top of the page): nothing must be typed afterwards.

---

## Part G — Prometheus controlling your Android phone (mobile-mcp)

Your laptop already has Node.js 24 and adb (checked on 25 Nov).

1. On the phone: **Settings → About phone** → tap **Build number** 7 times → enter your PIN (developer options on).
2. **Settings → System → Developer options** → switch on **USB debugging**.
3. Plug the phone into the laptop with a USB cable. On the phone press **Allow** for USB debugging (tick
   "Always allow from this computer").
4. **Keep the phone unlocked** during the check, and set the screen timeout to 10 minutes for now (Settings → Display).
5. In Prometheus: **Settings → Connections → Phone control → Connect mobile-mcp (Other apps)**.

**Checks** (say each in the chat, approve each question on the laptop):
- **open the calculator on my phone** → Calculator opens.
- **on my phone, tap 7, plus, 8, equals** → the phone shows 15 (several taps — not checked before).
- **open Notes on my phone and type shopping list** → the text appears (typing — not checked before).
- Open any app's login screen on the phone and ask **type my password there**: it must **refuse**.
- Every tap and every typed text must ask you first; reading the screen asks only the first time.

---

## Part H — Phone controls the laptop

**Not ready to try yet:** the pairing and the safety checks are built, but the phone app has no screen for it, so
there is nothing to press on the phone. Skip this part.

---

## Part I — Send back the results

Send a message with, for each part: **worked**, **didn't work** (with a screenshot), or **skipped**. Also say if any
question on the Approvals page didn't show you exactly what was about to happen. Those results replace the
"NOT VERIFIED" lines in `docs/reports/M6_REPORT.md`.

## If something goes wrong
- **Render build fails after B4:** open the service → **Logs**, take a screenshot of the last red lines.
- **"Cloudroom refused the signature":** the key in Prometheus and the Secret File on Render aren't the same, or the
  laptop's clock is wrong (Windows Settings → Time & language → **Set time automatically** on). Paste the key again in
  both places.
- **"Couldn't reach Cloudroom":** check `<your address>/v1/health` in the browser; wait a minute if Render was asleep.
- **Phone not found:** unplug and plug in again, unlock the phone, and press Allow on the USB debugging question.
