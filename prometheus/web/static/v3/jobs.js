/* The step diagram of a task graph (M5 5.5), drawn on a Canvas from the tracker's own data (data-graph).
   Colours are read from the design tokens, never written here. */
(function () {
  function token(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
  function colours() {
    return { done: token("--ok"), running: token("--orange"), waiting: token("--muted"), failed: token("--bad"),
             cancelled: token("--muted"), text: token("--text"), line: token("--muted"), base: token("--surface") };
  }
  function draw(canvas) {
    var g; try { g = JSON.parse(canvas.dataset.graph); } catch (e) { return; }
    var c = colours(), nodes = g.nodes || [], edges = g.edges || [];
    var levels = 0, perLevel = {}, rows = 1;
    nodes.forEach(function (n) {
      levels = Math.max(levels, n.level + 1);
      n.row = perLevel[n.level] = (perLevel[n.level] === undefined ? 0 : perLevel[n.level] + 1);
      rows = Math.max(rows, n.row + 1);
    });
    var colW = 130, rowH = 46, pad = 18, w = Math.max(colW * levels, 160), h = rows * rowH + pad;
    var dpr = window.devicePixelRatio || 1;
    canvas.width = w * dpr; canvas.height = h * dpr; canvas.style.width = w + "px"; canvas.style.height = h + "px";
    var x = canvas.getContext("2d"); x.setTransform(dpr, 0, 0, dpr, 0, 0); x.clearRect(0, 0, w, h);
    var pos = {};
    nodes.forEach(function (n) { pos[n.id] = { x: n.level * colW + colW / 2, y: n.row * rowH + pad }; });
    x.lineWidth = 1.5; x.strokeStyle = c.line; x.globalAlpha = 0.55;
    edges.forEach(function (e) {
      var a = pos[e[0]], b = pos[e[1]]; if (!a || !b) return;
      x.beginPath(); x.moveTo(a.x + 9, a.y); x.bezierCurveTo(a.x + 50, a.y, b.x - 50, b.y, b.x - 9, b.y); x.stroke();
    });
    x.globalAlpha = 1; x.font = "11px system-ui, sans-serif"; x.textAlign = "center";
    nodes.forEach(function (n) {
      var p = pos[n.id], col = c[n.state] || c.waiting;
      x.beginPath(); x.arc(p.x, p.y, 8, 0, Math.PI * 2);
      if (n.state === "waiting") { x.lineWidth = 2; x.strokeStyle = col; x.stroke(); }
      else if (n.state === "cancelled") { x.lineWidth = 2; x.setLineDash([3, 3]); x.strokeStyle = col; x.stroke(); x.setLineDash([]); }
      else { x.fillStyle = col; x.fill(); }
      x.fillStyle = c.text; x.fillText(n.label.length > 18 ? n.label.slice(0, 17) + "…" : n.label, p.x, p.y + 22);
    });
  }
  function drawAll() { document.querySelectorAll("canvas.job-graph").forEach(draw); }
  document.addEventListener("DOMContentLoaded", drawAll);
  document.body && drawAll();
  document.addEventListener("htmx:afterSwap", drawAll);
  window.addEventListener("resize", drawAll);
})();
