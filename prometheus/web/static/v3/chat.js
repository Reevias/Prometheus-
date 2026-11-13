/* Chat page extras: message buttons, code highlighting with copy/download, maths (KaTeX), diagrams and charts
   (Mermaid), steps that stay open across refreshes, and export. The libraries are local (static/vendor) and load
   only when a message needs them. */
(function () {
  "use strict";
  var msgs = document.getElementById("messages");
  if (!msgs) return;
  var chatId = msgs.dataset.chat, csrf = document.body.dataset.csrf || "";
  var toast = function (t, bad) { if (window.promToast) window.promToast(t, bad); };
  var copyText = function (t, done) { if (window.promCopy) window.promCopy(t, done); };
  var loaded = {};
  function load(src, css) {
    if (loaded[src]) return loaded[src];
    loaded[src] = new Promise(function (ok, fail) {
      if (css) { var l = document.createElement("link"); l.rel = "stylesheet"; l.href = css; document.head.appendChild(l); }
      var s = document.createElement("script"); s.src = src; s.onload = ok; s.onerror = fail; document.head.appendChild(s);
    });
    return loaded[src];
  }
  function raw(msg) { var t = msg.querySelector("template.raw"); return t ? t.content.textContent || t.innerHTML : ""; }
  var EXT = { python: "py", py: "py", javascript: "js", js: "js", typescript: "ts", ts: "ts", html: "html", css: "css",
              json: "json", bash: "sh", sh: "sh", shell: "sh", powershell: "ps1", ps1: "ps1", sql: "sql", java: "java",
              c: "c", cpp: "cpp", csharp: "cs", cs: "cs", go: "go", rust: "rs", ruby: "rb", php: "php", yaml: "yml",
              yml: "yml", xml: "xml", markdown: "md", md: "md", kotlin: "kt", swift: "swift", r: "r", lua: "lua" };

  function enhance() {
    // code blocks: a bar with the language, Copy and Download; highlighting once the library is there
    var codes = [].slice.call(msgs.querySelectorAll(".msg.assistant pre > code:not([data-done])"));
    codes.forEach(function (code) {
      code.dataset.done = "1";
      var pre = code.parentElement, lang = pre.dataset.lang || "";
      var bar = document.createElement("div"); bar.className = "code-bar";
      var name = document.createElement("span"); name.textContent = lang || "code"; bar.appendChild(name);
      var copy = document.createElement("button"); copy.type = "button"; copy.textContent = "Copy";
      copy.addEventListener("click", function () { copyText(code.textContent, "Code copied."); });
      var dl = document.createElement("button"); dl.type = "button"; dl.textContent = "Download";
      dl.addEventListener("click", function () {
        var a = document.createElement("a");
        a.href = URL.createObjectURL(new Blob([code.textContent], { type: "text/plain" }));
        a.download = "prometheus-code." + (EXT[lang] || "txt"); a.click(); setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
        toast("Downloaded " + a.download);
      });
      bar.appendChild(copy); bar.appendChild(dl); pre.insertBefore(bar, code);
    });
    if (codes.length) load("/static/vendor/highlight.min.js").then(function () {
      codes.forEach(function (c) { if (c.className.indexOf("language-") >= 0 && !c.dataset.hl) { c.dataset.hl = "1"; try { hljs.highlightElement(c); } catch (e) {} } });
    }).catch(function () {});
    // maths
    var maths = [].slice.call(msgs.querySelectorAll(".math:not([data-done])"));
    if (maths.length) load("/static/vendor/katex/katex.min.js", "/static/vendor/katex/katex.min.css").then(function () {
      maths.forEach(function (el) {
        el.dataset.done = "1";
        try { katex.render(el.textContent, el, { displayMode: el.classList.contains("block"), throwOnError: false, trust: false, strict: "ignore" }); } catch (e) {}
      });
    }).catch(function () {});
    // diagrams and charts
    var diagrams = [].slice.call(msgs.querySelectorAll("pre.diagram:not([data-done])"));
    if (diagrams.length) load("/static/vendor/mermaid.min.js").then(function () {
      var css = getComputedStyle(document.documentElement);
      var tok = function (n) { return "rgb(" + css.getPropertyValue(n).trim().split(/\s+/).join(",") + ")"; };
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "base", themeVariables: {
        background: tok("--base-rgb"), primaryColor: tok("--surface-rgb"), primaryTextColor: tok("--text-rgb"),
        primaryBorderColor: tok("--orange-rgb"), lineColor: tok("--amber-rgb"), secondaryColor: tok("--raised-rgb"),
        tertiaryColor: tok("--surface-rgb"), fontFamily: css.getPropertyValue("--font"),
        // charts: each slice/series its own palette colour, readable labels
        pie1: tok("--orange-rgb"), pie2: tok("--amber-rgb"), pie3: tok("--muted-rgb"), pie4: tok("--ok-rgb"),
        pie5: tok("--bad-rgb"), pie6: tok("--raised-rgb"), pieStrokeColor: tok("--base-rgb"),
        pieOuterStrokeColor: tok("--muted-rgb"), pieTitleTextColor: tok("--text-rgb"), pieSectionTextColor: tok("--base-rgb"),
        pieLegendTextColor: tok("--text-rgb"), pieOpacity: "0.95",
        xyChart: { backgroundColor: tok("--base-rgb"), titleColor: tok("--text-rgb"), xAxisLabelColor: tok("--muted-rgb"),
                   yAxisLabelColor: tok("--muted-rgb"), xAxisLineColor: tok("--muted-rgb"), yAxisLineColor: tok("--muted-rgb"),
                   plotColorPalette: [tok("--orange-rgb"), tok("--amber-rgb"), tok("--ok-rgb"), tok("--muted-rgb")].join(",") } } });
      diagrams.forEach(function (pre, i) {
        pre.dataset.done = "1";
        var id = "dg" + Date.now() + i;
        mermaid.render(id, pre.textContent).then(function (out) {
          var box = document.createElement("div"); box.className = "diagram-box"; box.innerHTML = out.svg; pre.replaceWith(box);
        }).catch(function () { pre.classList.add("diagram-bad"); var b = document.getElementById("d" + id); if (b) b.remove(); });
      });
    }).catch(function () {});
    // remember which step lists were open, so a refresh doesn't close them
    msgs.querySelectorAll("details.steps").forEach(function (d) {
      var key = (d.closest(".msg") || {}).id || "live";
      if (openSteps[key] !== undefined) d.open = openSteps[key];
      d.addEventListener("toggle", function () { openSteps[key] = d.open; });
    });
    tickElapsed();
  }
  var openSteps = {}, started = Date.now();
  function tickElapsed() {
    var el = msgs.querySelector("[data-elapsed]"); if (!el) { started = Date.now(); return; }
    el.textContent = Math.round((Date.now() - started) / 1000) + "s";
  }
  setInterval(tickElapsed, 1000);

  msgs.addEventListener("click", function (e) {
    var b = e.target.closest("[data-act]"); if (!b) return;
    var msg = b.closest(".msg"), act = b.dataset.act, input = document.getElementById("command-input");
    if (act === "copy") {
      copyText(msg.querySelector(".body").innerText, "Copied.");
    } else if (act === "edit" && input) {
      input.value = raw(msg); input.dispatchEvent(new Event("input")); input.focus();
    } else if (act === "regenerate" && input) {
      var prev = msg.previousElementSibling;
      while (prev && !prev.classList.contains("user")) prev = prev.previousElementSibling;
      if (!prev) { toast("There's no question before this answer.", true); return; }
      input.value = raw(prev); input.dispatchEvent(new Event("input"));
      document.getElementById("command").requestSubmit();
    } else if (act === "delete") {
      if (!confirm("Delete this message?")) return;
      fetch("/api/v3/chats/" + chatId + "/messages/" + msg.dataset.id + "/delete", { method: "POST", headers: { "X-CSRF-Token": csrf }, credentials: "same-origin" })
        .then(function (r) { return r.json().then(function (j) { return [r, j]; }); })
        .then(function (rj) { if (rj[0].ok) msg.remove(); else toast(rj[1].error || "Couldn't delete it.", true); });
    }
  });
  document.body.addEventListener("htmx:afterSwap", function (e) { if (e.target === msgs) enhance(); });
  enhance();

  // generated images: click to enlarge, save
  var box = document.getElementById("lightbox");
  document.addEventListener("click", function (e) {
    var b = e.target.closest(".out-img");
    if (b && box) {
      document.getElementById("lightbox-img").src = b.dataset.src; document.getElementById("lightbox-img").alt = b.dataset.name;
      var save = document.getElementById("lightbox-save"); save.href = b.dataset.src + "?download=1"; save.download = b.dataset.name;
      box.hidden = false; document.getElementById("lightbox-close").focus(); return;
    }
    if (box && !box.hidden && (e.target === box || e.target.id === "lightbox-close")) box.hidden = true;
  });
  document.addEventListener("keydown", function (e) { if (e.key === "Escape" && box && !box.hidden) { e.stopPropagation(); box.hidden = true; } }, true);

  // export menu in the top bar
  var exp = document.getElementById("export"), menu = document.getElementById("export-menu");
  if (exp) exp.addEventListener("click", function (e) { e.stopPropagation(); menu.hidden = !menu.hidden; exp.setAttribute("aria-expanded", String(!menu.hidden)); });
})();
