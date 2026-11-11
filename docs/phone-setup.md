# Prometheus on your phone

The phone always talks to the **cloud copy** (set it up first: docs/cloud-setup.md), so it works even when the
laptop is off. Anything that needs the laptop (its screen, files, apps) is passed to the laptop, which does it as
soon as it's on.

## 1. Install the app (2 minutes)
1. Install **Tailscale** on the phone (Play Store) and sign in with the same account as the cloud copy.
2. In Chrome, open the cloud copy's private address (https://prometheus-cloud.….ts.net) and sign in.
3. Chrome menu (⋮) → **Add to home screen** → Install. Prometheus now has its own icon and opens full-screen.
   (iPhone: Safari → Share → Add to Home Screen.)

## 2. Notifications
Open the app → ☰ menu → **Turn on phone notifications** → Allow. You get reminders, email and money alerts,
deadlines, the morning and evening summaries, and "waiting for your yes". Reminders that ring on the laptop reach
the phone too.

## 3. Talking
Hold the 🎤 button next to the message box and speak; let go to send. The answer is read out loud.
(This uses Chrome's own speech-to-text.)

## 4. Sending photos and files
Tap 📎 next to the message box: take a photo or pick a file (PDF, notes, slides, a recording).
- a receipt or bill → added to your spending
- a photo of your homework → checked step by step
- a PDF or notes → summarised (say "make flashcards" in the box first to get cards instead)
- a recording → lecture notes
Type what you want in the message box before tapping 📎 to tell Prometheus exactly what to do with it.
Photos you send are tagged and can be sorted like the laptop's ("sort my phone photos").

## 5. Bank SMS into the money tracker (Android)
Browsers can't read SMS, so a small free app forwards them:
1. Install **MacroDroid** (Play Store).
2. New macro → Trigger: **SMS Received** (any sender, or just your banks).
3. Action: **HTTP Request** → POST → URL:
   `https://prometheus-cloud.….ts.net/hooks/<your webhook secret>/sms`
   (the webhook secret is in Prometheus → Settings → Daily summary → Webhook)
   Body (JSON): `{"from": "[sms_number]", "text": "[sms_message]"}`
4. Save. Each bank or UPI SMS is added to your spending; other SMS are ignored.
