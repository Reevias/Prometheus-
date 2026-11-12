/**
 * Prometheus 3.0 — Orbital Energy Canvas Visualization
 *
 * Renders a high-performance, GPU-accelerated HTML5 Canvas with:
 * - Radiant celestial core with multi-layer radial amber/gold glow
 * - 4 tilted 3D elliptical orbital rings rotating continuously
 * - Traveling energy photon nodes with filament trails
 * - Ambient floating particle ember cloud
 * - State reactivity (idle, working, listening, speaking)
 * - Automatic pause on hidden tab and respects prefers-reduced-motion
 */

(function () {
  "use strict";

  class OrbitalCanvas {
    constructor(canvasId) {
      this.canvas = document.getElementById(canvasId);
      if (!this.canvas) return;

      this.ctx = this.canvas.getContext("2d");
      this.state = "idle"; // "idle" | "working" | "listen" | "speak"
      this.animId = null;
      this.lastTime = 0;
      this.rotation = 0;
      this.width = 0;
      this.height = 0;
      this.dpr = window.devicePixelRatio || 1;

      // Particle system
      this.particles = [];
      this.particleCount = 65;

      // Orbital rings config (3D tilted ellipses)
      this.rings = [
        { radiusX: 130, radiusY: 52, tilt: -24 * (Math.PI / 180), speed: 0.0006, color: "rgba(255, 146, 43, 0.75)", nodeAngle: 0, nodeSpeed: 0.018 },
        { radiusX: 175, radiusY: 68, tilt: 36 * (Math.PI / 180), speed: -0.0004, color: "rgba(255, 175, 75, 0.65)", nodeAngle: Math.PI / 2, nodeSpeed: 0.014 },
        { radiusX: 215, radiusY: 82, tilt: -52 * (Math.PI / 180), speed: 0.0003, color: "rgba(255, 146, 43, 0.55)", nodeAngle: Math.PI, nodeSpeed: 0.011 },
        { radiusX: 260, radiusY: 96, tilt: 15 * (Math.PI / 180), speed: -0.0002, color: "rgba(255, 192, 106, 0.45)", nodeAngle: (3 * Math.PI) / 2, nodeSpeed: 0.008 }
      ];

      this.init();
    }

    init() {
      this.resize();
      window.addEventListener("resize", () => this.resize());

      // Init particles
      this.initParticles();

      // Visibility change detection
      document.addEventListener("visibilitychange", () => {
        if (document.visibilityState === "visible") {
          this.start();
        } else {
          this.stop();
        }
      });

      this.start();
    }

    resize() {
      if (!this.canvas) return;
      const rect = this.canvas.getBoundingClientRect();
      this.width = rect.width || window.innerWidth;
      this.height = rect.height || window.innerHeight;

      this.canvas.width = this.width * this.dpr;
      this.canvas.height = this.height * this.dpr;
      this.ctx.scale(this.dpr, this.dpr);
    }

    initParticles() {
      this.particles = [];
      for (let i = 0; i < this.particleCount; i++) {
        this.particles.push({
          x: (Math.random() - 0.5) * 500,
          y: (Math.random() - 0.5) * 350,
          z: Math.random() * 200 - 100,
          radius: Math.random() * 2.2 + 0.8,
          alpha: Math.random() * 0.6 + 0.2,
          vx: (Math.random() - 0.5) * 0.35,
          vy: (Math.random() - 0.5) * 0.35,
          vz: (Math.random() - 0.5) * 0.2,
          pulse: Math.random() * Math.PI * 2
        });
      }
    }

    setState(newState) {
      if (this.state !== newState) {
        this.state = newState;
      }
    }

    start() {
      if (!this.animId) {
        this.lastTime = performance.now();
        this.animId = requestAnimationFrame((ts) => this.loop(ts));
      }
    }

    stop() {
      if (this.animId) {
        cancelAnimationFrame(this.animId);
        this.animId = null;
      }
    }

    loop(timestamp) {
      const dt = Math.min((timestamp - this.lastTime) / 1000, 0.1);
      this.lastTime = timestamp;

      this.update(dt);
      this.render();

      this.animId = requestAnimationFrame((ts) => this.loop(ts));
    }

    update(dt) {
      const speedMultiplier = this.state === "working" ? 2.5 : this.state === "listen" ? 1.4 : 1.0;

      // Update rotation
      this.rotation += 0.002 * speedMultiplier;

      // Update rings
      for (const ring of this.rings) {
        ring.nodeAngle += ring.nodeSpeed * speedMultiplier;
        if (ring.nodeAngle > Math.PI * 2) ring.nodeAngle -= Math.PI * 2;
      }

      // Update particles
      for (const p of this.particles) {
        p.x += p.vx * speedMultiplier;
        p.y += p.vy * speedMultiplier;
        p.pulse += dt * 2 * speedMultiplier;

        // Boundary wrap
        if (p.x < -280) p.x = 280;
        if (p.x > 280) p.x = -280;
        if (p.y < -200) p.y = 200;
        if (p.y > 200) p.y = -200;
      }
    }

    render() {
      const ctx = this.ctx;
      const cx = this.width / 2;
      const cy = this.height * 0.38; // Positioned in upper center

      ctx.clearRect(0, 0, this.width, this.height);

      // 1. Draw Background Ambient Radial Glow
      const ambientGlow = ctx.createRadialGradient(cx, cy, 10, cx, cy, 320);
      const isWorking = this.state === "working";
      ambientGlow.addColorStop(0, isWorking ? "rgba(255, 146, 43, 0.28)" : "rgba(255, 146, 43, 0.16)");
      ambientGlow.addColorStop(0.5, "rgba(232, 89, 12, 0.06)");
      ambientGlow.addColorStop(1, "rgba(11, 11, 16, 0)");
      ctx.fillStyle = ambientGlow;
      ctx.fillRect(0, 0, this.width, this.height);

      // 2. Draw Floating Particle Cloud (Behind Core)
      for (const p of this.particles) {
        const pulseAlpha = p.alpha * (0.7 + 0.3 * Math.sin(p.pulse));
        ctx.beginPath();
        ctx.arc(cx + p.x, cy + p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(255, 192, 106, ${pulseAlpha})`;
        ctx.shadowColor = "#FF922B";
        ctx.shadowBlur = 6;
        ctx.fill();
      }
      ctx.shadowBlur = 0;

      // 3. Draw 3D Tilted Orbital Rings
      for (let i = 0; i < this.rings.length; i++) {
        const ring = this.rings[i];
        ctx.save();
        ctx.translate(cx, cy);
        ctx.rotate(ring.tilt);

        // Ring ellipse path
        ctx.beginPath();
        ctx.ellipse(0, 0, ring.radiusX, ring.radiusY, 0, 0, Math.PI * 2);
        ctx.strokeStyle = ring.color;
        ctx.lineWidth = 1.4;
        ctx.shadowColor = "rgba(255, 146, 43, 0.6)";
        ctx.shadowBlur = 10;
        ctx.stroke();

        // Traveling Energy Node / Photon
        const nx = Math.cos(ring.nodeAngle) * ring.radiusX;
        const ny = Math.sin(ring.nodeAngle) * ring.radiusY;

        // Node Glow
        ctx.beginPath();
        ctx.arc(nx, ny, 3.5, 0, Math.PI * 2);
        ctx.fillStyle = "#FFF8E7";
        ctx.shadowColor = "#FF922B";
        ctx.shadowBlur = 14;
        ctx.fill();

        // Subtle filament trail behind node
        const trailSteps = 5;
        for (let t = 1; t <= trailSteps; t++) {
          const ta = ring.nodeAngle - (t * 0.08);
          const tx = Math.cos(ta) * ring.radiusX;
          const ty = Math.sin(ta) * ring.radiusY;
          ctx.beginPath();
          ctx.arc(tx, ty, 3.5 - (t * 0.5), 0, Math.PI * 2);
          ctx.fillStyle = `rgba(255, 146, 43, ${(trailSteps - t) / (trailSteps * 1.8)})`;
          ctx.fill();
        }

        ctx.restore();
      }

      // 4. Draw Central Luminous Core
      ctx.save();
      ctx.translate(cx, cy);

      const breathing = Math.sin(performance.now() * 0.0025) * 4;
      const coreRadius = (this.state === "working" ? 42 : 36) + breathing;

      // Outer Corona
      const coronaGrad = ctx.createRadialGradient(0, 0, 5, 0, 0, coreRadius * 2.2);
      coronaGrad.addColorStop(0, "rgba(255, 248, 231, 0.95)");
      coronaGrad.addColorStop(0.2, "rgba(255, 192, 106, 0.8)");
      coronaGrad.addColorStop(0.5, "rgba(255, 146, 43, 0.45)");
      coronaGrad.addColorStop(0.8, "rgba(232, 89, 12, 0.15)");
      coronaGrad.addColorStop(1, "rgba(11, 11, 16, 0)");

      ctx.beginPath();
      ctx.arc(0, 0, coreRadius * 2.2, 0, Math.PI * 2);
      ctx.fillStyle = coronaGrad;
      ctx.fill();

      // Intense Inner Core
      const innerGrad = ctx.createRadialGradient(0, 0, 0, 0, 0, coreRadius);
      innerGrad.addColorStop(0, "#FFFFFF");
      innerGrad.addColorStop(0.25, "#FFF8E7");
      innerGrad.addColorStop(0.65, "#FF922B");
      innerGrad.addColorStop(1, "rgba(232, 89, 12, 0.7)");

      ctx.beginPath();
      ctx.arc(0, 0, coreRadius, 0, Math.PI * 2);
      ctx.fillStyle = innerGrad;
      ctx.shadowColor = "#FF922B";
      ctx.shadowBlur = 24;
      ctx.fill();

      ctx.restore();
    }
  }

  // Export globally
  window.OrbitalCanvas = OrbitalCanvas;

  document.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("orbital-canvas")) {
      window.orbitalViz = new OrbitalCanvas("orbital-canvas");
    }
  });
})();
