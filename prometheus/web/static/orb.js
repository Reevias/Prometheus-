/* Prometheus orb: an ember core inside a turning wireframe globe, with orbiting sparks and flickering rays.
   One 2D canvas per orb, no libraries. It pauses when the tab is hidden or the orb is off screen, caps the frame
   rate, and draws a single still frame for people who ask for reduced motion.

   <canvas data-orb data-state="idle|busy|stopped"></canvas>   (state can be changed at any time) */
(function () {
  "use strict";
  const TAU = Math.PI * 2;
  const reduced = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const STATES = {
    idle:    { spin: 1.0, rays: 1.0, glow: 1.0, hue: 0 },
    busy:    { spin: 2.6, rays: 1.6, glow: 1.35, hue: 0 },
    stopped: { spin: 0.25, rays: 0.25, glow: 0.55, hue: 1 },
    listen:  { spin: 1.8, rays: 1.9, glow: 1.5, hue: 0 },     // heard "Prometheus": wide awake
    speak:   { spin: 1.4, rays: 1.3, glow: 1.25, hue: 0, pulse: 1 },
  };

  function rand(seed) {            // small fixed random, so every orb looks the same on every load
    let s = seed >>> 0;
    return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; };
  }

  function makeOrb(canvas) {
    const ctx = canvas.getContext("2d");
    const big = !canvas.hasAttribute("data-small");
    const rnd = rand(big ? 7 : 11);
    const N = big ? 220 : 110;                 // globe points
    const pts = [];
    const golden = Math.PI * (3 - Math.sqrt(5));
    for (let i = 0; i < N; i++) {              // even spread over a sphere (Fibonacci lattice)
      const y = 1 - (i / (N - 1)) * 2, r = Math.sqrt(1 - y * y), a = golden * i;
      pts.push([Math.cos(a) * r, y, Math.sin(a) * r]);
    }
    const links = [];                          // wire each point to its two nearest neighbours
    for (let i = 0; i < N; i++) {
      const d = [];
      for (let j = 0; j < N; j++) if (j !== i) {
        const dx = pts[i][0] - pts[j][0], dy = pts[i][1] - pts[j][1], dz = pts[i][2] - pts[j][2];
        d.push([dx * dx + dy * dy + dz * dz, j]);
      }
      d.sort((a, b) => a[0] - b[0]);
      for (let k = 0; k < 2; k++) if (d[k][1] > i) links.push([i, d[k][1]]);
    }
    const sparks = [];                         // particles on three tilted orbits
    const S = big ? 420 : 160;
    for (let i = 0; i < S; i++) {
      sparks.push({ ring: i % 3, a: rnd() * TAU, r: 1.08 + rnd() * 0.32, v: 0.25 + rnd() * 0.5,
                    s: 0.4 + rnd() * 1.3, j: (rnd() - 0.5) * 0.12 });
    }
    const RINGS = [[0.35, 0.0], [1.1, 0.9], [-0.7, 2.1]];   // tilt, turn of each orbit
    const rays = [];
    const R = big ? 22 : 12;
    for (let i = 0; i < R; i++) rays.push({ a: rnd() * TAU, len: 0.9 + rnd() * 0.9, w: 0.6 + rnd() * 1.2,
                                            f: 0.6 + rnd() * 2.4, p: rnd() * TAU });

    let w = 0, h = 0, dpr = 1, visible = true, last = 0, t = rnd() * 100, raf = 0;
    const minGap = big ? 1000 / 45 : 1000 / 24;

    function size() {
      dpr = Math.min(window.devicePixelRatio || 1, 1.5);
      const box = canvas.getBoundingClientRect();
      w = Math.max(1, box.width); h = Math.max(1, box.height);
      canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }

    function colour(alpha, heat, grey) {     // heat 0..1: deep ember to white-hot
      if (grey) { const g = Math.round(110 + heat * 90); return `rgba(${g},${g},${g + 8},${alpha})`; }
      const r = 255, g = Math.round(55 + heat * heat * 170), b = Math.round(8 + heat * heat * heat * 140);
      return `rgba(${r},${g},${b},${alpha})`;
    }

    function frame(now) {
      raf = 0;
      if (!visible || document.hidden) return;
      if (!reduced) raf = requestAnimationFrame(frame);
      if (now - last < minGap && !reduced) return;
      const dt = last ? Math.min(0.05, (now - last) / 1000) : 0.016;
      last = now;
      const st = STATES[canvas.dataset.state] || STATES.idle;
      t += dt * st.spin;
      const grey = st.hue === 1;
      const cx = w / 2, cy = h / 2, rad = Math.min(w, h) * 0.27;
      const edge = Math.min(w, h) / 2 * 0.98;     // nothing is drawn past the canvas edge, so no hard cut-off lines

      ctx.globalCompositeOperation = "source-over";
      ctx.clearRect(0, 0, w, h);
      ctx.globalCompositeOperation = "lighter";

      // haze behind everything
      let g = ctx.createRadialGradient(cx, cy, 0, cx, cy, edge);
      g.addColorStop(0, colour(0.22 * st.glow, 0.5, grey));
      g.addColorStop(0.45, colour(0.07 * st.glow, 0.2, grey));
      g.addColorStop(1, colour(0, 0, grey));
      ctx.fillStyle = g; ctx.fillRect(0, 0, w, h);

      // rays
      for (const ray of rays) {
        const flick = 0.55 + 0.45 * Math.sin(t * ray.f + ray.p) * Math.sin(t * ray.f * 0.37 + ray.p * 2);
        const a = ray.a + t * 0.05;
        const len = Math.min(edge, rad * (0.7 + ray.len * flick * st.rays));
        const x1 = cx + Math.cos(a) * rad * 0.15, y1 = cy + Math.sin(a) * rad * 0.15;
        const x2 = cx + Math.cos(a) * len, y2 = cy + Math.sin(a) * len;
        const lg = ctx.createLinearGradient(x1, y1, x2, y2);
        lg.addColorStop(0, colour(0.9 * flick, 0.8, grey));
        lg.addColorStop(0.55, colour(0.45 * flick, 0.35, grey));
        lg.addColorStop(1, colour(0, 0.1, grey));
        ctx.strokeStyle = lg; ctx.lineWidth = ray.w * (big ? 1 : 0.7);
        ctx.beginPath(); ctx.moveTo(x1, y1); ctx.lineTo(x2, y2); ctx.stroke();
      }

      // turning wireframe globe
      const ry = t * 0.35, rx = 0.42 + Math.sin(t * 0.13) * 0.12;
      const cyR = Math.cos(ry), syR = Math.sin(ry), cxR = Math.cos(rx), sxR = Math.sin(rx);
      const proj = pts.map(([x, y, z]) => {
        const x1 = x * cyR + z * syR, z1 = -x * syR + z * cyR;
        const y2 = y * cxR - z1 * sxR, z2 = y * sxR + z1 * cxR;
        return [cx + x1 * rad, cy + y2 * rad, z2];
      });
      ctx.lineWidth = big ? 0.7 : 0.5;
      for (const [i, j] of links) {
        const depth = (proj[i][2] + proj[j][2]) / 2;           // -1 back, 1 front
        ctx.strokeStyle = colour(0.12 + 0.3 * (depth + 1) / 2, 0.45, grey);
        ctx.beginPath(); ctx.moveTo(proj[i][0], proj[i][1]); ctx.lineTo(proj[j][0], proj[j][1]); ctx.stroke();
      }
      for (const p of proj) {
        const k = (p[2] + 1) / 2;
        ctx.fillStyle = colour(0.25 + 0.6 * k, 0.5 + 0.4 * k, grey);
        ctx.fillRect(p[0] - 0.8, p[1] - 0.8, 1.6, 1.6);
      }

      // a bright sweeping arc, like a scanner
      ctx.strokeStyle = colour(0.55 * st.glow, 0.7, grey);
      ctx.lineWidth = big ? 3 : 2;
      ctx.beginPath();
      ctx.ellipse(cx, cy, rad * 1.02, rad * 0.98, t * 0.6, -0.4, 1.6);
      ctx.stroke();

      // sparks on three tilted orbits
      for (const s of sparks) {
        s.a += dt * s.v * st.spin;
        const [tilt, turn] = RINGS[s.ring];
        const r = rad * (s.r + s.j * Math.sin(t + s.a * 3));
        let x = Math.cos(s.a) * r, y = Math.sin(s.a) * r * Math.cos(tilt);
        const z = Math.sin(s.a) * Math.sin(tilt);
        const ct = Math.cos(turn + t * 0.05), stt = Math.sin(turn + t * 0.05);
        const xr = x * ct - y * stt, yr = x * stt + y * ct;
        const k = (z + 1) / 2;
        ctx.fillStyle = colour(0.25 + 0.65 * k, 0.6 + 0.4 * k, grey);
        const sz = s.s * (big ? 1 : 0.7);
        ctx.fillRect(cx + xr - sz / 2, cy + yr - sz / 2, sz, sz);
      }

      // white-hot core
      const pulse = 1 + (st.pulse ? 0.16 * Math.abs(Math.sin(t * 6.5)) : 0.08 * Math.sin(t * 2.2));
      g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad * 0.62 * pulse * st.glow);
      g.addColorStop(0, grey ? "rgba(235,235,240,0.95)" : "rgba(255,250,225,1)");
      g.addColorStop(0.18, colour(0.9, 0.95, grey));
      g.addColorStop(0.45, colour(0.35, 0.6, grey));
      g.addColorStop(1, colour(0, 0.2, grey));
      ctx.fillStyle = g;
      ctx.beginPath(); ctx.arc(cx, cy, rad * 0.62 * pulse * st.glow, 0, TAU); ctx.fill();
    }

    function wake() { if (!raf && visible && !document.hidden) raf = requestAnimationFrame(frame); }
    size();
    if ("ResizeObserver" in window) new ResizeObserver(() => { size(); if (reduced) frame(performance.now()); else wake(); }).observe(canvas);
    if ("IntersectionObserver" in window) {
      new IntersectionObserver((e) => { visible = e[0].isIntersecting; wake(); }).observe(canvas);
    }
    document.addEventListener("visibilitychange", wake);
    new MutationObserver(() => { if (reduced) frame(performance.now()); }).observe(canvas, { attributes: true, attributeFilter: ["data-state"] });
    wake();
  }

  function start() { document.querySelectorAll("canvas[data-orb]").forEach((c) => { if (!c._orb) { c._orb = true; makeOrb(c); } }); }
  window.PrometheusOrb = { start };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
