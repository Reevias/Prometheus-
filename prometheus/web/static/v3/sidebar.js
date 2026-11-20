/* The sidebar (laptop check, 20 Nov 2026): Search, More, projects that fold (remembered on this device), and the
   ⋯ menu of a chat (rename, move to project, pin, delete). Lists come from /api/v3/sidebar and are fetched again on
   "sidebar-refresh". No browser prompt(): the boxes are the page's own <dialog>s. */
(function () {
  "use strict";
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return [].slice.call((r || document).querySelectorAll(s)); };
  var lists = $("#side-lists");
  if (!lists) return;
  var csrf = document.body.dataset.csrf || "";
  var toast = function (t, bad) { if (window.promToast) window.promToast(t, bad); };
  function post(url, fields) {
    var body = new FormData();
    Object.keys(fields || {}).forEach(function (k) { body.append(k, fields[k]); });
    return fetch(url, { method: "POST", body: body, credentials: "same-origin",
                        headers: { "X-CSRF-Token": csrf, "HX-Current-URL": location.href } });
  }
  function refresh() { document.body.dispatchEvent(new Event("sidebar-refresh")); }
  function openChat() { var m = location.pathname.match(/^\/c\/(\d+)/); return m ? m[1] : ""; }

  // the microphone next to Stop all: voice on or off
  var mic = $("#mic-toggle");
  if (mic) mic.addEventListener("click", function () {
    post("/api/v3/voice/toggle").then(function (r) { return r.json(); }).then(function (j) {
      if (j.error) { toast(j.error, true); return; }
      mic.classList.toggle("off", !j.on); mic.setAttribute("aria-pressed", j.on ? "true" : "false");
      mic.title = j.on ? "Voice is on: click to turn it off" : "Voice is off: click to turn it on";
      toast(j.on ? "Voice is on." : "Voice is off: the microphone isn't listening.");
    }).catch(function () { toast("Couldn't switch voice.", true); });
  });

  // Search = the Ctrl+K palette
  var search = $("#side-search");
  if (search) search.addEventListener("click", function () {
    if (window.promDrawer) window.promDrawer(false);           // on a phone the palette would open under the drawer
    if (window.promOpenPalette) window.promOpenPalette();
  });

  // More pages
  var moreBtn = $("#nav-more-btn"), more = $("#nav-more");
  if (moreBtn && more) moreBtn.addEventListener("click", function (e) {
    e.stopPropagation();
    var open = more.hidden; hideMenus(); more.hidden = !open; moreBtn.setAttribute("aria-expanded", open ? "true" : "false");
    if (!open) return;
    var narrow = $("#sidebar").getBoundingClientRect().width < 120;   // the icon sidebar: open beside it
    if (narrow) {
      var r = moreBtn.getBoundingClientRect(), side = $("#sidebar").getBoundingClientRect();
      more.style.position = "fixed"; more.style.left = (side.right + 6) + "px";
      more.style.top = Math.min(r.top, window.innerHeight - more.offsetHeight - 8) + "px"; more.style.right = "auto";
    } else { more.style.position = more.style.left = more.style.top = more.style.right = ""; }
    var first = $("a", more); if (first) first.focus({ preventScroll: true });
  });

  // projects fold; which ones are open is remembered on this device
  var KEY = "prom-open-projects";
  function openSet() { try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch (e) { return []; } }
  function saveOpen() {
    var ids = $$("details.side-project[open]", lists).map(function (d) { return d.dataset.project; });
    try { localStorage.setItem(KEY, JSON.stringify(ids)); } catch (e) {}
  }
  function afterLists() {
    var keep = openSet();
    $$("details.side-project", lists).forEach(function (d) {
      if (keep.indexOf(d.dataset.project) >= 0 || $("a.side-chat.on", d)) d.open = true;
      d.addEventListener("toggle", saveOpen);
    });
  }
  lists.addEventListener("htmx:afterSwap", afterLists);
  lists.addEventListener("click", function (e) {
    var open = e.target.closest(".side-open");
    if (open) { e.stopPropagation(); return; }                 // the project page, not a fold
    var add = e.target.closest("[data-new-in]");
    if (add) {
      post(add.dataset.newIn).then(function (r) { return r.json(); }).then(function (j) {
        if (j && j.url) location.href = j.url; else toast((j && j.error) || "Couldn't start a chat.", true);
      }).catch(function () { toast("Couldn't start a chat.", true); });
      return;
    }
    var dots = e.target.closest("[data-chat-menu]");
    if (dots) { e.preventDefault(); e.stopPropagation(); showChatMenu(dots); }
  });

  // the ⋯ menu of a chat
  var menu = $("#chat-menu"), current = null;
  function hideMenus() {
    if (menu) menu.hidden = true;
    if (more) { more.hidden = true; if (moreBtn) moreBtn.setAttribute("aria-expanded", "false"); }
  }
  document.addEventListener("click", function (e) { if (!e.target.closest("#chat-menu, #nav-more, #nav-more-btn")) hideMenus(); });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") hideMenus(); });
  function showChatMenu(btn) {
    if (!menu) return;
    var row = btn.closest(".side-row"), proj = btn.closest("details.side-project");
    current = { id: btn.dataset.chatMenu, title: btn.dataset.title || "", project: proj ? proj.dataset.project : "" };
    var pinned = row && row.dataset.pinned === "1";
    $("[data-act=pin] span", menu).textContent = pinned ? "Unpin" : "Pin";
    var r = btn.getBoundingClientRect();
    menu.hidden = false;
    menu.style.top = Math.min(r.bottom + 4, window.innerHeight - menu.offsetHeight - 8) + "px";
    menu.style.left = Math.min(r.left, window.innerWidth - menu.offsetWidth - 8) + "px";
  }
  if (menu) menu.addEventListener("click", function (e) {
    var act = e.target.closest("[data-act]");
    if (!act || !current) return;
    var c = current; hideMenus();
    if (act.dataset.act === "rename") return ask("#rename-dialog", c);
    if (act.dataset.act === "move") return ask("#move-dialog", c);
    if (act.dataset.act === "pin") {
      return post("/api/v3/chats/" + c.id + "/pin").then(refresh);
    }
    if (act.dataset.act === "delete") {
      if (!confirm("Delete the chat “" + c.title + "” and everything in it?")) return;
      post("/chats/" + c.id + "/delete").then(function (r) {
        if (!r.ok) { r.text().then(function (t) { toast(t || "Couldn't delete it.", true); }); return; }
        if (openChat() === String(c.id)) location.href = "/v3/chats";
        else if (location.pathname === "/v3/chats") location.reload();
        else refresh();
      });
    }
  });

  // rename and move boxes
  function ask(sel, c) {
    var box = $(sel); if (!box) return;
    box.dataset.chat = c.id; box.dataset.project = c.project;
    var input = $("input", box);
    if (input) { input.value = c.title; }
    var select = $("select", box);
    if (select) {
      var data = $("#side-projects-data", lists), all = [];
      try { all = JSON.parse(data ? data.dataset.projects : "[]"); } catch (e) {}
      select.innerHTML = "";
      var none = document.createElement("option"); none.value = ""; none.textContent = "No project"; select.appendChild(none);
      all.forEach(function (p) { var o = document.createElement("option"); o.value = p[0]; o.textContent = p[1]; select.appendChild(o); });
      select.value = c.project || "";
    }
    box.showModal(); (input || select).focus(); if (input) input.select();
  }
  $$("dialog.side-dialog").forEach(function (box) {
    $("[data-cancel]", box).addEventListener("click", function () { box.close(); });
    $("form", box).addEventListener("submit", function (e) {
      e.preventDefault();
      var id = box.dataset.chat, from = box.dataset.project, done;
      if (box.id === "rename-dialog") {
        var title = $("input", box).value.trim(); if (!title) return;
        done = post("/chats/" + id + "/rename", { title: title }).then(function () {
          if (openChat() === String(id)) location.reload(); else refresh();
        });
      } else {
        var to = $("select", box).value;
        if (to === (from || "")) { box.close(); return; }
        done = (to ? post("/api/v3/projects/" + to + "/chats/" + id, { on: "1" })
                   : post("/api/v3/projects/" + from + "/chats/" + id, { on: "0" })).then(refresh);
      }
      done.then(function () { box.close(); }).catch(function () { toast("That didn't work.", true); });
    });
  });
})();
