/* The orbital sphere: a glowing core inside a sphere of glowing lines, with a particle cloud around it.
   Every <canvas data-sphere="hero|panel|mini"> on the page gets one. Colours come from tokens.css.

   States (window.PromSphere.setState): idle (slow, breathing), thinking (faster, brighter), tool (amber shift and
   pulse rings), done (short bright flash), error (short red flash), listening/speaking (follows .level(0..1)).

   Light on the laptop: quality adapts by itself (fewer points, lower frame rate) when drawing gets slow, the choice
   can be fixed in Settings (localStorage "prom-quality": low|medium|high|auto), drawing pauses while the window is
   hidden or not focused, and with "reduce motion" one still frame is drawn. */
(function () {
  "use strict";
  var css = getComputedStyle(document.documentElement);
  function rgb(name, fallback) { return (css.getPropertyValue(name).trim() || fallback).split(/\s+/).map(Number); }
  var ORANGE = rgb("--orange-rgb", "255 146 43"), AMBER = rgb("--amber-rgb", "255 192 106"),
      WHITE = rgb("--text-rgb", "244 241 237"), RED = rgb("--bad-rgb", "255 107 91");
  function col(c, a) { return "rgba(" + c[0] + "," + c[1] + "," + c[2] + "," + a.toFixed(3) + ")"; }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t].map(Math.round); }

  var QUALITY = { low: { pts: 0.4, fps: 24, dpr: 1 }, medium: { pts: 0.7, fps: 30, dpr: 1.5 }, high: { pts: 1, fps: 60, dpr: 2 } };
  var KIND = { hero: { pts: 230, dust: 320, chords: 70, rays: 7 }, panel: { pts: 110, dust: 90, chords: 20, rays: 4 },
               mini: { pts: 46, dust: 0, chords: 10, rays: 2 } };
  var SPEED = { idle: 0.16, thinking: 0.5, tool: 0.34, done: 0.2, error: 0.12, listening: 0.24, speaking: 0.28 };

  function pref() { try { return localStorage.getItem("prom-quality") || "auto"; } catch (e) { return "auto"; } }
  function stillOnly() {
    var m = document.documentElement.dataset.motion;
    if (m === "reduced") return true;
    if (m === "full") return false;
    return window.matchMedia && matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  var weakPC = (navigator.deviceMemory && navigator.deviceMemory <= 8) || (navigator.hardwareConcurrency || 8) <= 4;
  var quality = pref() !== "auto" ? pref() : (weakPC ? "medium" : "high");

  var state = "idle", flash = 0, flashColor = WHITE, level = 0, levelSmooth = 0;

  function Sphere(canvas) {
    this.c = canvas; this.ctx = canvas.getContext("2d"); this.kind = KIND[canvas.dataset.sphere] || KIND.panel;
    this.build(); this.resize();
    var self = this;
    if (window.ResizeObserver) new ResizeObserver(function () { self.resize(); }).observe(canvas);
  }
  Sphere.prototype.build = function () {
    var q = QUALITY[quality] || QUALITY.medium, n = Math.max(24, Math.round(this.kind.pts * q.pts)), i;
    this.p = [];
    var golden = Math.PI * (3 - Math.sqrt(5));
    for (i = 0; i < n; i++) {                                  // even points on a sphere (Fibonacci)
      var y = 1 - (i / (n - 1)) * 2, r = Math.sqrt(1 - y * y), t = golden * i;
      this.p.push([Math.cos(t) * r, y, Math.sin(t) * r]);
    }
    this.e = [];                                               // each point to its 3 nearest: the line mesh
    for (i = 0; i < n; i++) {
      var d = [];
      for (var j = 0; j < n; j++) if (j !== i) {
        var a = this.p[i], b = this.p[j];
        d.push([(a[0] - b[0]) * (a[0] - b[0]) + (a[1] - b[1]) * (a[1] - b[1]) + (a[2] - b[2]) * (a[2] - b[2]), j]);
      }
      d.sort(function (x, y) { return x[0] - y[0]; });
      for (var k = 0; k < 3; k++) if (d[k][1] > i) this.e.push([i, d[k][1]]);
    }
    for (i = 0; i < Math.round(this.kind.chords * q.pts); i++)   // long chords straight through the sphere
      this.e.push([Math.floor(Math.random() * n), Math.floor(Math.random() * n), 1]);
    this.dust = [];
    for (i = 0; i < Math.round(this.kind.dust * q.pts); i++) {
      var u = Math.random() * 2 - 1, th = Math.random() * Math.PI * 2, rr = 1.04 + Math.pow(Math.random(), 1.6) * 0.75,
          s = Math.sqrt(1 - u * u);
      this.dust.push([Math.cos(th) * s * rr, u * rr, Math.sin(th) * s * rr, Math.random() * 6.28, 0.4 + Math.random() * 1.2]);
    }
    this.rays = [];
    for (i = 0; i < this.kind.rays; i++) this.rays.push([Math.random() * 6.28, 0.6 + Math.random() * 0.9, Math.random()]);
  };
  Sphere.prototype.resize = function () {
    var q = QUALITY[quality] || QUALITY.medium, dpr = Math.min(window.devicePixelRatio || 1, q.dpr);
    var w = Math.max(1, Math.round(this.c.clientWidth * dpr)), h = Math.max(1, Math.round(this.c.clientHeight * dpr));
    if (this.c.width !== w || this.c.height !== h) { this.c.width = w; this.c.height = h; }
    this.dpr = dpr;
    if (stillOnly()) this.draw(0);
  };
  Sphere.prototype.draw = function (t) {
    var ctx = this.ctx, W = this.c.width, H = this.c.height, cx = W / 2, cy = H / 2;
    var R = Math.min(W, H) * (this.kind === KIND.hero ? 0.37 : this.kind === KIND.mini ? 0.36 : 0.32);
    ctx.clearRect(0, 0, W, H);
    var breathe = 1 + Math.sin(t * 1.4) * 0.035 + levelSmooth * 0.12;
    var hot = state === "thinking" ? 1.25 : state === "tool" ? 1.12 : 1;
    var base = state === "tool" ? mix(ORANGE, AMBER, 0.55) : ORANGE;
    var ang = t * (SPEED[state] || 0.16), tilt = 0.42, ca = Math.cos(ang), sa = Math.sin(ang), ct = Math.cos(tilt), st = Math.sin(tilt);
    function proj(v) {
      var x = v[0] * ca - v[2] * sa, z = v[0] * sa + v[2] * ca, y = v[1] * ct - z * st, z2 = v[1] * st + z * ct;
      return [cx + x * R * breathe, cy + y * R * breathe, z2];
    }
    ctx.globalCompositeOperation = "lighter";
    // halo
    var edge = Math.min(W, H) / 2;                            // everything fades out before the canvas edge
    var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, edge);
    g.addColorStop(0, col(base, 0.30 * hot)); g.addColorStop(0.5, col(base, 0.09 * hot)); g.addColorStop(1, col(base, 0));
    ctx.fillStyle = g; ctx.fillRect(0, 0, W, H);
    // dust cloud
    var lw = Math.max(1, this.dpr);
    for (var i = 0; i < this.dust.length; i++) {
      var d = this.dust[i], q = proj(d), a = (0.25 + 0.5 * Math.max(0, Math.sin(t * d[4] + d[3]))) * (q[2] > 0 ? 1 : 0.5);
      ctx.fillStyle = col(i % 5 ? base : AMBER, a * 0.8 * hot);
      ctx.fillRect(q[0], q[1], lw * (q[2] > 0 ? 1.6 : 1), lw * (q[2] > 0 ? 1.6 : 1));
    }
    // line mesh: front lines brighter
    var pr = this.p.map(proj);
    ctx.lineWidth = lw * (this.kind === KIND.mini ? 0.9 : 0.8);
    for (i = 0; i < this.e.length; i++) {
      var A = pr[this.e[i][0]], B = pr[this.e[i][1]], depth = (A[2] + B[2]) / 2;
      var alpha = (this.e[i][2] ? 0.2 : 0.55) * (0.35 + 0.65 * (depth + 1) / 2) * hot;
      ctx.strokeStyle = col(base, alpha);
      ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.stroke();
    }
    for (i = 0; i < pr.length; i++) if (pr[i][2] > -0.2) {
      ctx.fillStyle = col(AMBER, 0.35 + 0.55 * (pr[i][2] + 1) / 2);
      ctx.beginPath(); ctx.arc(pr[i][0], pr[i][1], lw * (pr[i][2] > 0.5 ? 1.5 : 1), 0, 6.283); ctx.fill();
    }
    // rays from the core
    for (i = 0; i < this.rays.length; i++) {
      var ry = this.rays[i], ra = ry[0] + Math.sin(t * 0.2 + ry[2] * 6) * 0.05;
      var len = Math.min(R * ry[1] * 1.9 * (1 + 0.1 * Math.sin(t + ry[2] * 9)), edge * 0.97);
      var ex = cx + Math.cos(ra) * len, ey = cy + Math.sin(ra) * len;
      var lg = ctx.createLinearGradient(cx, cy, ex, ey);
      lg.addColorStop(0, col(AMBER, 0.6 * hot)); lg.addColorStop(0.6, col(base, 0.18 * hot)); lg.addColorStop(1, col(base, 0));
      ctx.strokeStyle = lg; ctx.lineWidth = lw * 1.2;
      ctx.beginPath(); ctx.moveTo(cx, cy); ctx.lineTo(ex, ey); ctx.stroke();
    }
    // tool runs: pulse rings
    if (state === "tool") {
      var ph = (t * 0.6) % 1;
      ctx.strokeStyle = col(AMBER, 0.45 * (1 - ph)); ctx.lineWidth = lw * 1.5;
      ctx.beginPath(); ctx.arc(cx, cy, R * (0.5 + ph * 0.9), 0, 6.283); ctx.stroke();
    }
    // the core
    var cr = R * (0.42 + 0.05 * Math.sin(t * 1.8)) * hot * (1 + levelSmooth * 0.35);
    var cg = ctx.createRadialGradient(cx, cy, 0, cx, cy, cr);
    cg.addColorStop(0, col(WHITE, 0.98)); cg.addColorStop(0.16, col(mix(WHITE, AMBER, 0.5), 0.9));
    cg.addColorStop(0.42, col(base, 0.55)); cg.addColorStop(1, col(base, 0));
    ctx.fillStyle = cg; ctx.beginPath(); ctx.arc(cx, cy, cr, 0, 6.283); ctx.fill();
    if (flash > 0) {                                           // done / error: brief flash, then settle
      var fg = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.6);
      fg.addColorStop(0, col(flashColor, 0.55 * flash)); fg.addColorStop(1, col(flashColor, 0));
      ctx.fillStyle = fg; ctx.fillRect(0, 0, W, H);
    }
    ctx.globalCompositeOperation = "source-over";
  };

  var spheres = [], last = 0, slow = 0, start = performance.now(), running = false;
  function frame(now) {
    if (!running) return;
    requestAnimationFrame(frame);
    var q = QUALITY[quality] || QUALITY.medium;
    if (now - last < 1000 / q.fps - 1) return;
    var dt = (now - last) / 1000; last = now;
    levelSmooth += (level - levelSmooth) * 0.25;
    if (flash > 0) { flash = Math.max(0, flash - dt * 1.6); if (!flash && (state === "done" || state === "error")) state = "idle"; }
    var t0 = performance.now(), t = (now - start) / 1000;
    for (var i = 0; i < spheres.length; i++) if (spheres[i].c.isConnected && spheres[i].c.clientWidth) spheres[i].draw(t);
    var took = performance.now() - t0;                         // too slow for this PC: step quality down
    slow = took > 14 ? slow + 1 : Math.max(0, slow - 1);
    if (slow > 20 && pref() === "auto" && quality !== "low") {
      quality = quality === "high" ? "medium" : "low"; slow = 0;
      spheres.forEach(function (s) { s.build(); s.resize(); });
    }
  }
  function play() {
    if (stillOnly()) { running = false; spheres.forEach(function (s) { s.draw(1.2); }); return; }
    if (!running && !document.hidden) { running = true; last = 0; requestAnimationFrame(frame); }
  }
  function pause() { running = false; }

  function init() {
    document.querySelectorAll("canvas[data-sphere]").forEach(function (c) { if (!c._sphere) { c._sphere = new Sphere(c); spheres.push(c._sphere); } });
    play();
  }
  document.addEventListener("visibilitychange", function () { document.hidden ? pause() : play(); });
  window.addEventListener("blur", pause);
  window.addEventListener("focus", play);
  if (window.matchMedia) matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", function () { pause(); play(); });

  window.PromSphere = {
    setState: function (s) {
      if (s === "done" || s === "error") { flash = 1; flashColor = s === "error" ? RED : WHITE; }
      if (s !== state) { state = s; if (stillOnly()) spheres.forEach(function (x) { x.draw(1.2); }); }
    },
    level: function (v) { level = Math.max(0, Math.min(1, v || 0)); },
    setQuality: function (q) {
      try { localStorage.setItem("prom-quality", q); } catch (e) {}
      quality = q === "auto" ? (weakPC ? "medium" : "high") : q;
      spheres.forEach(function (s) { s.build(); s.resize(); });
    },
    refresh: function () { pause(); init(); }
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
