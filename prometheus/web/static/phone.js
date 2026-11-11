// Phone extras: install, notifications, hold-to-talk, and sending photos/files from the composer.
(function () {
  const csrf = () => { try { return JSON.parse(document.body.getAttribute("hx-headers"))["X-CSRF-Token"]; } catch (_) { return ""; } };
  if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});

  // notifications: asked once, from a tap (browsers require that)
  window.promNotify = async function () {
    if (!("serviceWorker" in navigator) || !("PushManager" in window)) { alert("This browser can't show notifications from Prometheus. Install it to the home screen first."); return; }
    const perm = await Notification.requestPermission();
    if (perm !== "granted") return;
    const reg = await navigator.serviceWorker.ready;
    const key = await (await fetch("/push/key")).text();
    const raw = atob(key.replace(/-/g, "+").replace(/_/g, "/") + "==".slice(0, (4 - key.length % 4) % 4));
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: Uint8Array.from(raw, (c) => c.charCodeAt(0)) });
    const r = await fetch("/push/subscribe", { method: "POST", headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() }, body: JSON.stringify(sub) });
    const b = document.getElementById("notify-btn"); if (b && r.ok) { b.textContent = "Notifications on ✓"; b.disabled = true; }
  };

  // hold to talk: the phone's own speech-to-text; the answer is read out loud
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  window.promTalkStart = function (btn) {
    if (!Rec) { alert("This browser can't listen. Try Chrome."); return; }
    const r = new Rec(); r.lang = "en-IN"; r.interimResults = false; r.maxAlternatives = 1;
    btn.classList.add("live"); btn._rec = r;
    r.onresult = (e) => {
      const text = e.results[0][0].transcript;
      const form = document.getElementById("command"); if (!form) return;
      form.querySelector("textarea").value = text; window.promSpeakNext = true; form.requestSubmit();
    };
    r.onend = () => btn.classList.remove("live");
    r.start();
  };
  window.promTalkStop = function (btn) { if (btn._rec) btn._rec.stop(); };
  document.body.addEventListener("htmx:afterSwap", (e) => {
    if (!window.promSpeakNext || !("speechSynthesis" in window)) return;
    const msgs = document.querySelectorAll("#messages .msg.assistant");
    const last = msgs[msgs.length - 1];
    if (!last || last.dataset.spoken || document.querySelector("#messages .working")) return;
    last.dataset.spoken = "1"; window.promSpeakNext = false;
    const u = new SpeechSynthesisUtterance(last.innerText.slice(0, 600)); u.lang = "en-IN"; speechSynthesis.speak(u);
  });
})();
