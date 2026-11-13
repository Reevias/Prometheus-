/* Prometheus 3.0 interface: command bar, attachments, palettes, shortcuts, live right panel, window buttons. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var csrf = document.body.dataset.csrf || "";
  var data = {};
  try { data = JSON.parse(($("#v3-data") || {}).textContent || "{}"); } catch (e) { data = {}; }
  var app = $("#app");

  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function post(url, body) {
    return fetch(url, { method: "POST", headers: { "X-CSRF-Token": csrf }, body: body || new FormData(), credentials: "same-origin" });
  }
  function toast(text, bad) {
    var box = $("#toasts"); if (!box) return;
    var t = document.createElement("div"); t.className = "toast" + (bad ? " bad" : ""); t.textContent = text;
    box.appendChild(t); setTimeout(function () { t.remove(); }, bad ? 7000 : 4000);
  }
  window.promToast = toast;
  function visible() { return document.visibilityState === "visible"; }
  function closeMenus(except) {
    var closed = false;
    $$(".menu, .dropdown, #slash, #palette").forEach(function (m) {
      if (m !== except && !m.hidden) { m.hidden = true; closed = true; }
    });
    $$("[aria-expanded=true]").forEach(function (b) { b.setAttribute("aria-expanded", "false"); });
    return closed;
  }
  document.addEventListener("click", function (e) {
    if (!e.target.closest(".menu, .dropdown, #plus, #more, #bell, #slash, #palette, #command-input")) closeMenus();
  });

  // ---- sidebar and right panel: shrink / hide (remembered on this computer) ----------------------------------
  if (store("prom-mini") === "1") app.classList.add("mini");
  if (store("prom-panel") === "0") app.classList.add("no-panel");
  var collapse = $("#collapse");
  if (collapse) collapse.addEventListener("click", function () {
    app.classList.toggle("mini"); store("prom-mini", app.classList.contains("mini") ? "1" : "0");
  });
  var panelToggle = $("#panel-toggle");
  if (panelToggle) panelToggle.addEventListener("click", function () {
    app.classList.toggle("no-panel"); store("prom-panel", app.classList.contains("no-panel") ? "0" : "1");
  });

  // ---- greeting ----------------------------------------------------------------------------------------------
  var greet = $("#greeting");
  if (greet) {
    var h = new Date().getHours(), part = h < 5 ? "Good night" : h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    greet.textContent = part + (greet.dataset.owner ? ", " + greet.dataset.owner : "") + ". Your personal AI system.";
  }
  var setName = $("#set-name");
  if (setName) setName.addEventListener("click", function () {
    var name = (prompt("What should Prometheus call you?") || "").trim();
    if (!name) return;
    var f = new FormData(); f.append("name", name);
    post("/api/v3/profile", f).then(function (r) { if (r.ok) location.reload(); else toast("Couldn't save your name.", true); });
  });

  // ---- command bar ------------------------------------------------------------------------------------------
  var form = $("#command"), input = $("#command-input"), send = $("#send"), fileInput = $("#file-input");
  var files = [], busy = false, queued = null;
  var chatId = form ? form.dataset.chat || "" : "";
  var SEND_ICON = send ? send.innerHTML : "";
  var STOP_ICON = '<svg viewBox="0 0 24 24" fill="none" stroke-width="1.7" aria-hidden="true"><rect x="7" y="7" width="10" height="10" rx="1.5"/></svg>';

  function grow() { if (!input) return; input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, input.clientHeight && parseFloat(getComputedStyle(input).maxHeight) || 9999) + "px"; }
  function sendState() {
    if (!send) return;
    var ready = (input.value.trim() || files.length) && !busy;
    send.classList.toggle("ready", !!ready);
    send.classList.toggle("stop", busy);
    send.innerHTML = busy ? STOP_ICON : SEND_ICON;
    send.title = busy ? "Stop the reply (Esc)" : "Send (Enter)";
    send.setAttribute("aria-label", busy ? "Stop the reply" : "Send");
  }
  function setBusy(b) {
    if (b === busy) return;
    busy = b; sendState();
    if (!b && queued) { var q = queued; queued = null; $("#queued").hidden = true; submit(q.text, q.files); }
  }
  function fmtSize(n) { return n < 1024 ? n + " B" : n < 1048576 ? (n / 1024).toFixed(0) + " KB" : (n / 1048576).toFixed(1) + " MB"; }
  function renderFiles() {
    var box = $("#files"); if (!box) return;
    box.innerHTML = ""; box.hidden = !files.length;
    files.forEach(function (f, i) {
      var chip = document.createElement("div"); chip.className = "file";
      var ext = (f.name.split(".").pop() || "").slice(0, 4);
      if (/^image\//.test(f.type)) { var img = document.createElement("img"); img.alt = ""; img.src = URL.createObjectURL(f); chip.appendChild(img); }
      else { var ico = document.createElement("span"); ico.className = "ico"; ico.textContent = ext; chip.appendChild(ico); }
      var meta = document.createElement("div"); meta.className = "meta";
      var b = document.createElement("b"); b.textContent = f.name; meta.appendChild(b);
      var s = document.createElement("span"); s.textContent = fmtSize(f.size) + " · " + (ext || "file").toUpperCase(); meta.appendChild(s);
      if (/^text\/|\.(md|csv|txt|py|js|json|ts|html|css|log)$/i.test(f.type + " " + f.name) && f.size < 200000) {
        var pre = document.createElement("pre"); meta.appendChild(pre);
        f.slice(0, 300).text().then(function (t) { pre.textContent = t; });
      }
      chip.appendChild(meta);
      var x = document.createElement("button"); x.type = "button"; x.textContent = "×"; x.title = "Remove " + f.name;
      x.setAttribute("aria-label", "Remove " + f.name);
      x.addEventListener("click", function () { files.splice(i, 1); renderFiles(); sendState(); });
      chip.appendChild(x); box.appendChild(chip);
    });
    if (files.length > 1) {
      var clear = document.createElement("button"); clear.type = "button"; clear.className = "clear"; clear.textContent = "Clear all";
      clear.addEventListener("click", function () { files = []; renderFiles(); sendState(); }); box.appendChild(clear);
    }
  }
  function addFiles(list) { Array.prototype.forEach.call(list || [], function (f) { files.push(f); }); renderFiles(); sendState(); if (input) input.focus(); }

  function submit(text, extra) {
    text = (text || "").trim(); extra = extra || [];
    if (!text && !extra.length) return;
    var body = new FormData(); body.append("text", text); if (chatId) body.append("chat_id", chatId);
    extra.forEach(function (f) { body.append("files", f, f.name); });
    if (send) send.disabled = true;
    post("/api/v3/send", body).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) { return [r, j]; });
    }).then(function (rj) {
      var r = rj[0], j = rj[1];
      if (!r.ok) { toast(j.error || "Couldn't send that.", true); return; }
      if (String(j.chat_id) === String(chatId)) { setBusy(true); if (window.htmx) htmx.trigger("#messages", "refresh"); refreshMessages(); }
      else location.href = j.url;
    }).catch(function () { toast("Prometheus isn't answering. Is it still running?", true); })
      .finally(function () { if (send) send.disabled = false; });
  }
  function refreshMessages() {
    var m = $("#messages"); if (!m || !window.htmx) return;
    htmx.ajax("GET", "/c/" + m.dataset.chat + "/messages", { target: "#messages", swap: "innerHTML" });
  }
  function stopReply() {
    if (!chatId) return;
    post("/api/v3/chats/" + chatId + "/stop").then(function () { toast("Stopping after the current step…"); });
  }
  if (form) {
    form.addEventListener("submit", function (e) {
      e.preventDefault();
      if (busy) { stopReply(); return; }
      var text = input.value, extra = files.slice();
      if (!text.trim() && !extra.length) return;
      store("prom-last", text);
      input.value = ""; files = []; renderFiles(); grow(); sendState();
      submit(text, extra);
    });
    input.addEventListener("input", function () { grow(); sendState(); slashUpdate(); });
    input.addEventListener("keydown", function (e) {
      if (slashKeys(e)) return;
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
        e.preventDefault();
        if (busy && (input.value.trim() || files.length)) {         // queue it: sent when the reply finishes
          queued = { text: input.value, files: files.slice() };
          $("#queued").textContent = "Queued: sends when this reply finishes"; $("#queued").hidden = false;
          input.value = ""; files = []; renderFiles(); grow(); sendState(); return;
        }
        form.requestSubmit();
      } else if (e.key === "ArrowUp" && !input.value) {             // edit the last message
        var last = store("prom-last"); if (last) { e.preventDefault(); input.value = last; grow(); sendState(); }
      }
    });
    var plus = $("#plus"), plusMenu = $("#plus-menu");
    plus.addEventListener("click", function () {
      var open = plusMenu.hidden; closeMenus(); plusMenu.hidden = !open; plus.setAttribute("aria-expanded", String(open));
    });
    $$("button[data-accept]", plusMenu).forEach(function (b) {
      b.addEventListener("click", function () {
        fileInput.accept = b.dataset.accept === "*" ? "" : b.dataset.accept; plusMenu.hidden = true; fileInput.click();
      });
    });
    fileInput.addEventListener("change", function () { addFiles(fileInput.files); fileInput.value = ""; });
    var chip = $("#model-chip");
    if (chip) chip.addEventListener("change", function () {
      var f = new FormData(); f.append("head", chip.value);
      post("/settings/head", f).then(function (r) { toast(r.ok ? "Answers now come from: " + chip.options[chip.selectedIndex].text : "Couldn't switch the AI.", !r.ok); });
    });
    sendState();
  }
  // drag and drop anywhere
  var drop = $("#drop"), depth = 0;
  window.addEventListener("dragenter", function (e) { if (!form || !e.dataTransfer || [].indexOf.call(e.dataTransfer.types, "Files") < 0) return; depth++; drop.hidden = false; });
  window.addEventListener("dragleave", function () { depth = Math.max(0, depth - 1); if (!depth) drop.hidden = true; });
  window.addEventListener("dragover", function (e) { if (form) e.preventDefault(); });
  window.addEventListener("drop", function (e) { if (!form) return; e.preventDefault(); depth = 0; drop.hidden = true; addFiles(e.dataTransfer.files); });

  // quick actions: fill the command bar
  function fill(text) { if (!input) return; input.value = text; input.focus(); input.setSelectionRange(text.length, text.length); grow(); sendState(); }
  $$("[data-fill]").forEach(function (b) { b.addEventListener("click", function () { closeMenus(); fill(b.dataset.fill); }); });
  var more = $("#more"), moreMenu = $("#more-menu");
  if (more) more.addEventListener("click", function () {
    var open = moreMenu.hidden; closeMenus(); moreMenu.hidden = !open; more.setAttribute("aria-expanded", String(open));
    var r = more.getBoundingClientRect(), pr = more.parentElement.getBoundingClientRect();
    moreMenu.style.left = (r.left - pr.left) + "px"; moreMenu.style.top = (r.bottom - pr.top + 8) + "px";
  });

  // ---- "/" commands ----------------------------------------------------------------------------------------
  var slash = $("#slash"), slashList = $("#slash-list"), slashSel = 0, slashItems = [];
  function runCommand(c) {
    closeMenus();
    if (c.kind === "fill") fill(c.value);
    else if (c.kind === "go") location.href = c.value;
    else if (c.kind === "do" && c.value === "stop") { if (input) input.value = ""; busy ? stopReply() : toast("Nothing is running in this chat."); sendState(); }
  }
  function drawList(listEl, items, sel, onPick) {
    listEl.innerHTML = "";
    if (!items.length) { var n = document.createElement("div"); n.className = "none"; n.textContent = "No match"; listEl.appendChild(n); return; }
    var group = null;
    items.forEach(function (c, i) {
      if (c.group !== group) { group = c.group; var g = document.createElement("div"); g.className = "group"; g.textContent = group; listEl.appendChild(g); }
      var b = document.createElement("button"); b.type = "button"; b.className = "item" + (i === sel ? " sel" : "");
      b.setAttribute("role", "option"); b.setAttribute("aria-selected", String(i === sel));
      if (c.command) { var code = document.createElement("code"); code.textContent = c.command; b.appendChild(code); }
      var bb = document.createElement("b"); bb.textContent = c.label; b.appendChild(bb);
      var sp = document.createElement("span"); sp.textContent = c.description || ""; b.appendChild(sp);
      b.addEventListener("mousedown", function (e) { e.preventDefault(); onPick(c); });
      listEl.appendChild(b);
    });
    var cur = listEl.querySelector(".sel"); if (cur) cur.scrollIntoView({ block: "nearest" });
  }
  function slashUpdate(force) {
    if (!slash || !input) return;
    var v = input.value;
    if (!force && !/^\/\S*$/.test(v)) { slash.hidden = true; return; }
    var q = v.slice(1).toLowerCase();
    slashItems = (data.commands || []).filter(function (c) {
      return !q || c.command.slice(1).indexOf(q) === 0 || c.label.toLowerCase().indexOf(q) >= 0;
    });
    slashSel = Math.min(slashSel, Math.max(0, slashItems.length - 1));
    slash.hidden = false; drawList(slashList, slashItems, slashSel, runCommand);
  }
  function slashKeys(e) {
    if (!slash || slash.hidden) return false;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault(); slashSel = (slashSel + (e.key === "ArrowDown" ? 1 : -1) + slashItems.length) % Math.max(1, slashItems.length);
      drawList(slashList, slashItems, slashSel, runCommand); return true;
    }
    if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); if (slashItems[slashSel]) runCommand(slashItems[slashSel]); return true; }
    if (e.key === "Escape") { e.preventDefault(); slash.hidden = true; return true; }
    return false;
  }

  // ---- Ctrl+K: search everything --------------------------------------------------------------------------
  var pal = $("#palette"), palInput = $("#palette-input"), palList = $("#palette-list"), palSel = 0, palItems = [];
  function palAll() {
    var out = $$("#nav a").map(function (a) { return { group: "Pages", label: a.textContent.trim(), description: "Open", kind: "go", value: a.getAttribute("href") }; });
    out.push({ group: "Pages", label: "Settings", description: "Open", kind: "go", value: "/settings" });
    (data.chats || []).forEach(function (c) { out.push({ group: "Chats", label: c[1], description: "Open chat", kind: "go", value: "/c/" + c[0] }); });
    out.push({ group: "Chats", label: "Search all chats", description: "Chats page", kind: "go", value: "/v3/chats" });
    (data.commands || []).forEach(function (c) { out.push(Object.assign({}, c, { group: "Commands" })); });
    return out;
  }
  function palDraw() {
    var q = palInput.value.trim().toLowerCase();
    palItems = palAll().filter(function (c) { return !q || (c.label + " " + (c.command || "") + " " + (c.description || "")).toLowerCase().indexOf(q) >= 0; });
    palSel = Math.min(palSel, Math.max(0, palItems.length - 1));
    drawList(palList, palItems, palSel, function (c) { pal.hidden = true; if (c.kind === "fill" && !input) location.href = "/"; else runCommand(c); });
  }
  function openPalette() { closeMenus(); pal.hidden = false; palInput.value = ""; palSel = 0; palDraw(); palInput.focus(); }
  if (pal) {
    palInput.addEventListener("input", function () { palSel = 0; palDraw(); });
    palInput.addEventListener("keydown", function (e) {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); palSel = (palSel + (e.key === "ArrowDown" ? 1 : -1) + palItems.length) % Math.max(1, palItems.length); palDraw(); }
      else if (e.key === "Enter") { e.preventDefault(); var c = palItems[palSel]; if (c) { pal.hidden = true; runCommand(c); } }
      else if (e.key === "Escape") { e.preventDefault(); pal.hidden = true; }
    });
  }

  // ---- keyboard shortcuts --------------------------------------------------------------------------------
  document.addEventListener("keydown", function (e) {
    var mod = e.ctrlKey || e.metaKey;
    if (mod && e.key.toLowerCase() === "k") { e.preventDefault(); openPalette(); return; }
    if (mod && e.key === "/") { e.preventDefault(); if (input) { input.focus(); if (!input.value) input.value = "/"; slashUpdate(true); } return; }
    if (mod && e.key.toLowerCase() === "l") { e.preventDefault(); if (input) input.focus(); return; }
    if (mod && e.key.toLowerCase() === "n") { e.preventDefault(); location.href = "/"; return; }
    if (mod && /^[0-9]$/.test(e.key)) {
      var n = e.key === "0" ? 10 : +e.key, link = $('#nav a[data-key="' + n + '"]');
      if (link) { e.preventDefault(); location.href = link.getAttribute("href"); } return;
    }
    if (e.key === "F11") { e.preventDefault(); win("fullscreen"); return; }
    if (e.key === "Escape") {                         // menus first, then the reply, then fullscreen
      if (closeMenus()) { e.preventDefault(); return; }
      if (busy) { e.preventDefault(); stopReply(); return; }
      if (desktop && fullscreen) { win("fullscreen"); fullscreen = false; }
    }
    if ((e.key === "ArrowDown" || e.key === "ArrowUp") && document.activeElement && document.activeElement.closest("#nav")) {
      e.preventDefault(); var links = $$("#nav a"), i = links.indexOf(document.activeElement);
      links[(i + (e.key === "ArrowDown" ? 1 : -1) + links.length) % links.length].focus();
    }
  });

  // ---- desktop window buttons (only inside the Prometheus window, never in a browser) ------------------------
  var desktop = false, fullscreen = true;
  function win(action) { if (!desktop) return; post("/api/v3/window/" + action).then(function (r) { if (!r.ok) toast("The window didn't respond.", true); }); }
  function desktopReady() { desktop = true; var w = $("#win-controls"); if (w) w.hidden = false; }
  if (window.pywebview) desktopReady(); else window.addEventListener("pywebviewready", desktopReady);
  $$("[data-win]").forEach(function (b) { b.addEventListener("click", function () { win(b.dataset.win); if (b.dataset.win === "maximize") fullscreen = false; }); });

  // ---- notifications ---------------------------------------------------------------------------------------
  var bell = $("#bell"), notes = $("#notes");
  if (bell) bell.addEventListener("click", function () {
    var open = notes.hidden; closeMenus(); if (!open) return;
    notes.hidden = false; bell.setAttribute("aria-expanded", "true");
    fetch("/api/v3/notifications", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (list) {
      var box = $("#notes-list"); box.innerHTML = "";
      if (!list.length) { box.innerHTML = '<div class="note muted">No notifications yet.</div>'; return; }
      list.forEach(function (n) {
        var d = document.createElement("div"); d.className = "note" + (n.read ? "" : " unread");
        var w = document.createElement("div"); w.className = "when"; w.textContent = new Date(n.at * 1000).toLocaleString();
        var t = document.createElement("div"); t.textContent = n.text; d.appendChild(t); d.appendChild(w); box.appendChild(d);
      });
      post("/api/v3/notifications/read"); $("#bell-badge").hidden = true;
    });
  });

  // ---- voice: mic button, global mute, live waveform ---------------------------------------------------------
  var mic = $("#mic"), mute = $("#mute"), voice = { available: false }, voiceFast = null;
  function voiceLevels(level) {
    var bars = $$("#levels i"), on = voice.state === "listening" || voice.state === "speaking";
    bars.forEach(function (b, i) {
      var h = on ? 4 + Math.round(22 * level * (0.45 + 0.55 * Math.abs(Math.sin(Date.now() / 140 + i * 1.7)))) : 4;
      b.style.height = h + "px";
    });
    if (window.PromSphere) PromSphere.level(on ? level : 0);
  }
  function showVoice(v) {
    voice = v || { available: false };
    if (mute) {
      var running = voice.available && voice.state && voice.state !== "off" && voice.state !== "setup" && voice.state !== "error";
      mute.hidden = !running;
      mute.classList.toggle("off", !!voice.muted);
      mute.setAttribute("aria-pressed", String(!!voice.muted));
      mute.title = voice.muted ? "Microphone muted: click to unmute (Ctrl+Shift+M)" : "Microphone on: click to mute (Ctrl+Shift+M)";
    }
    if (mic) mic.classList.toggle("on", voice.state === "listening");
    var live = voice.state === "listening" || voice.state === "speaking";
    if (live && !voiceFast) voiceFast = setInterval(function () {
      fetch("/api/v3/voice", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (v) {
        voice = v; voiceLevels(v.level || 0);
        if (window.PromSphere && (v.state === "listening" || v.state === "speaking")) PromSphere.setState(v.state);
        if (v.state !== "listening" && v.state !== "speaking") { clearInterval(voiceFast); voiceFast = null; voiceLevels(0); showVoice(v); }
      }).catch(function () {});
    }, 150);
    if (!live) voiceLevels(0);
  }
  function listen() {
    post("/api/v3/voice/listen").then(function (r) { return r.json().then(function (j) { return [r, j]; }); }).then(function (rj) {
      if (!rj[0].ok) { toast(rj[1].error || "Voice didn't start.", true); return; }
      toast(rj[1].starting ? "Starting voice… speak after the chime." : "Listening… speak after the chime.");
    }).catch(function () { toast("Voice didn't start.", true); });
  }
  if (mic) mic.addEventListener("click", listen);
  if (mute) mute.addEventListener("click", function () {
    post("/api/v3/voice/mute").then(function (r) { return r.json(); }).then(function (j) {
      if (j.error) { toast(j.error, true); return; }
      voice.muted = j.muted; showVoice(voice);
      toast(j.muted ? "Microphone muted." : "Microphone on.");
    });
  });
  document.addEventListener("keydown", function (e) {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "m") {
      e.preventDefault();
      if (e.shiftKey) { if (mute && !mute.hidden) mute.click(); } else if (mic) listen();
    }
  });

  // ---- right panel: real readings ---------------------------------------------------------------------------
  var GAUGE = 'M 8 46 A 35 35 0 0 1 78 46';
  function gauge(label, pct, text) {
    var len = 110, off = len - len * Math.max(0, Math.min(100, pct)) / 100;
    return '<div class="gauge"><svg viewBox="0 0 86 52"><path class="track" d="' + GAUGE + '" fill="none" stroke-width="6" stroke-linecap="round"/>' +
      '<path class="fill" d="' + GAUGE + '" fill="none" stroke-width="6" stroke-linecap="round" stroke-dasharray="' + len + '" stroke-dashoffset="' + off.toFixed(1) + '"/></svg>' +
      '<div class="label">' + label + '</div><div class="value">' + text + '</div></div>';
  }
  function telemetry() {
    if (!visible() || !$("#gauges") || app.classList.contains("no-panel")) return;
    fetch("/api/v3/telemetry", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (t) {
      var g = [];
      if (t.cpu != null) g.push(gauge("CPU", t.cpu, Math.round(t.cpu) + "%"));
      if (t.ram) g.push(gauge("RAM", t.ram.percent, Math.round(t.ram.percent) + "%"));
      if (t.gpu) g.push(gauge("GPU", t.gpu.percent, Math.round(t.gpu.percent) + "%"));
      if (t.temp_c != null) g.push(gauge("Temp", t.temp_c, Math.round(t.temp_c) + "°"));
      if (t.disk && g.length < 6) g.push(gauge("Disk", t.disk.percent, Math.round(t.disk.percent) + "%"));
      $("#gauges").innerHTML = g.join("");
      var f = [];
      if (t.ram) f.push(["Memory", t.ram.used_gb.toFixed(1) + " / " + t.ram.total_gb.toFixed(1) + " GB"]);
      if (t.disk) f.push(["Disk free", t.disk.free_gb + " GB"]);
      if (t.net) f.push(["Network", "↓ " + t.net.down_kbps + " KB/s  ↑ " + t.net.up_kbps + " KB/s"]);
      if (t.gpu) f.push(["GPU", t.gpu.model]);
      $("#facts").innerHTML = f.map(function (x) { return "<dt>" + x[0] + "</dt><dd>" + x[1].replace(/</g, "&lt;") + "</dd>"; }).join("");
      $("#system-card").hidden = !g.length && !f.length;
    }).catch(function () {});
  }
  var lastTask = null;
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : String(s); return d.innerHTML; }
  function state() {
    if (!visible()) return;
    fetch("/api/v3/state", { credentials: "same-origin" }).then(function (r) { return r.json(); }).then(function (s) {
      var dot = $("#online-dot"); if (dot) { dot.className = "dot " + (s.online ? "ok" : s.status === "paused" ? "warn" : "bad"); $("#online-label").textContent = s.status_label; }
      var badge = $("#bell-badge"); if (badge) badge.hidden = !s.unread;
      showVoice(s.voice);
      var a = s.active_agent, t = s.current_task;
      if ($("#agent-box")) $("#agent-box").innerHTML = a ? '<div><div class="who">' + esc(a.name) + '</div><div class="what">' + esc(a.description) + '</div></div><span class="spinner" aria-hidden="true"></span>' : '<span class="idle">Nothing running</span>';
      if ($("#task-box")) $("#task-box").innerHTML = t ? '<div>' + esc(t.title) + '</div>' + (t.progress != null ?
        '<div class="progress"><div class="track"><i style="width:' + t.progress + '%"></i></div><span>' + Math.round(t.progress) + '%</span></div>' :
        '<div class="what muted">' + esc(t.detail) + '</div>') : '<span class="idle">No task running</span>';
      var mine = t && String(t.chat_id) === String(chatId);
      setBusy(!!mine);
      if (window.PromSphere && s.voice && (s.voice.state === "listening" || s.voice.state === "speaking")) PromSphere.setState(s.voice.state);
      else if (window.PromSphere) {
        if (t) PromSphere.setState(a && a.name !== "Planning" ? "tool" : "thinking");
        else if (lastTask) PromSphere.setState("done");
        else PromSphere.setState("idle");
      }
      lastTask = t;
    }).catch(function () {
      var dot = $("#online-dot"); if (dot) { dot.className = "dot bad"; $("#online-label").textContent = "Can't reach Prometheus"; }
    });
  }
  state(); telemetry();
  setInterval(state, 2000); setInterval(telemetry, 3000);
  document.addEventListener("visibilitychange", function () { if (visible()) { state(); telemetry(); } });

  // ---- chat page: keep the newest message in view; copy buttons ---------------------------------------------
  var msgs = $("#messages");
  if (msgs) {
    msgs.scrollTop = msgs.scrollHeight;
    document.body.addEventListener("htmx:afterSwap", function (e) { if (e.target === msgs) { msgs.scrollTop = msgs.scrollHeight; setBusy(!!$(".working", msgs)); } });
    msgs.addEventListener("click", function (e) {
      var b = e.target.closest("[data-copy]"); if (!b) return;
      var body = b.closest(".msg").querySelector(".body");
      navigator.clipboard.writeText(body.innerText).then(function () { toast("Copied."); });
    });
    setBusy(!!$(".working", msgs));
  }
  if (input && input.autofocus) input.focus();
})();
