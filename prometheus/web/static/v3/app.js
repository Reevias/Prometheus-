/**
 * Prometheus 3.0 — Frontend Application Orchestration
 *
 * Connects UI components to real-time backend state, telemetry,
 * command submission, slash palette, quick actions, and window controls.
 */

(function () {
  "use strict";

  const GAUGE_CIRCUMFERENCE = 2 * Math.PI * 28; // r=28 -> ~175.93

  class PrometheusV3App {
    constructor() {
      this.csrfToken = document.body.getAttribute("data-csrf") || "";
      this.slashCommands = [];
      this.selectedSlashIndex = 0;
      this.isSubmitting = false;

      this.initElements();
      this.bindEvents();
      this.initSlashCommands();
      this.startTelemetryLoop();
      this.startStateLoop();
    }

    initElements() {
      this.cmdInput = document.getElementById("command-input");
      this.cmdForm = document.getElementById("command-form");
      this.slashIndicator = document.getElementById("slash-indicator");
      this.micBtn = document.getElementById("mic-btn");
      this.submitBtn = document.getElementById("submit-btn");

      // Palette
      this.paletteOverlay = document.getElementById("slash-palette-overlay");
      this.paletteSearch = document.getElementById("palette-search");
      this.paletteList = document.getElementById("palette-list");

      // Gauges
      this.cpuFill = document.getElementById("cpu-gauge-fill");
      this.cpuVal = document.getElementById("cpu-gauge-val");
      this.ramFill = document.getElementById("ram-gauge-fill");
      this.ramVal = document.getElementById("ram-gauge-val");
      this.gpuFill = document.getElementById("gpu-gauge-fill");
      this.gpuVal = document.getElementById("gpu-gauge-val");

      // Active Agent & Task
      this.agentName = document.getElementById("active-agent-name");
      this.agentDesc = document.getElementById("active-agent-desc");
      this.agentBadge = document.getElementById("active-agent-badge");
      this.taskTitle = document.getElementById("current-task-title");
      this.taskPercent = document.getElementById("current-task-percent");
      this.taskFill = document.getElementById("current-task-fill");
      this.taskDetail = document.getElementById("current-task-detail");

      // Window controls
      this.winMin = document.getElementById("win-min-btn");
      this.winMax = document.getElementById("win-max-btn");
      this.winClose = document.getElementById("win-close-btn");
    }

    bindEvents() {
      // Command submission
      if (this.cmdForm) {
        this.cmdForm.addEventListener("submit", (e) => {
          e.preventDefault();
          this.submitCommand();
        });
      }

      // Enter key submission
      if (this.cmdInput) {
        this.cmdInput.addEventListener("keydown", (e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            this.submitCommand();
          } else if (e.key === "/" && (this.cmdInput.value === "" || this.cmdInput.selectionStart === 0)) {
            // Open slash command palette
            setTimeout(() => this.openSlashPalette(), 10);
          }
        });
      }

      // Slash indicator click
      if (this.slashIndicator) {
        this.slashIndicator.addEventListener("click", () => this.openSlashPalette());
      }

      // Palette search filtering
      if (this.paletteSearch) {
        this.paletteSearch.addEventListener("input", (e) => this.filterSlashCommands(e.target.value));
        this.paletteSearch.addEventListener("keydown", (e) => this.handlePaletteKeydown(e));
      }

      // Close palette on backdrop click
      if (this.paletteOverlay) {
        this.paletteOverlay.addEventListener("click", (e) => {
          if (e.target === this.paletteOverlay) {
            this.closeSlashPalette();
          }
        });
      }

      // Global Escape
      document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          this.closeSlashPalette();
        }
      });

      // Quick action pills
      document.querySelectorAll(".quick-action-pill, .quick-actions .chip").forEach((pill) => {
        pill.addEventListener("click", () => {
          const action = pill.getAttribute("data-action");
          this.handleQuickAction(action);
        });
      });

      // Window controls
      if (this.winMin) this.winMin.addEventListener("click", () => this.sendWindowAction("minimize"));
      if (this.winMax) this.winMax.addEventListener("click", () => this.sendWindowAction("maximize"));
      if (this.winClose) this.winClose.addEventListener("click", () => this.sendWindowAction("close"));

      // Mic button
      if (this.micBtn) {
        this.micBtn.addEventListener("click", () => this.toggleVoice());
      }
    }

    async initSlashCommands() {
      try {
        const resp = await fetch("/api/v3/slash-commands");
        if (resp.ok) {
          this.slashCommands = await resp.json();
          this.renderPaletteList(this.slashCommands);
        }
      } catch (err) {
        console.warn("Failed to load slash commands", err);
      }
    }

    openSlashPalette() {
      if (!this.paletteOverlay) return;
      this.paletteOverlay.hidden = false;
      if (this.paletteSearch) {
        this.paletteSearch.value = "";
        this.paletteSearch.focus();
      }
      this.selectedSlashIndex = 0;
      this.renderPaletteList(this.slashCommands);
    }

    closeSlashPalette() {
      if (!this.paletteOverlay) return;
      this.paletteOverlay.hidden = true;
      if (this.cmdInput) {
        this.cmdInput.focus();
      }
    }

    renderPaletteList(commands) {
      if (!this.paletteList) return;
      this.paletteList.innerHTML = "";

      if (commands.length === 0) {
        this.paletteList.innerHTML = '<div style="padding:12px;color:var(--text-muted);font-size:13px;text-align:center;">No matching commands</div>';
        return;
      }

      commands.forEach((cmd, idx) => {
        const item = document.createElement("div");
        item.className = `palette-item ${idx === this.selectedSlashIndex ? "selected" : ""}`;
        item.innerHTML = `
          <div class="palette-item-left">
            <span class="palette-cmd-badge">${cmd.command}</span>
            <span class="palette-cmd-desc">${cmd.description}</span>
          </div>
          <span class="palette-cmd-shortcut">${cmd.label}</span>
        `;
        item.addEventListener("click", () => {
          this.applyCommand(cmd.command);
        });
        this.paletteList.appendChild(item);
      });
    }

    filterSlashCommands(query) {
      const q = (query || "").toLowerCase().trim();
      const filtered = this.slashCommands.filter(
        (c) => c.command.toLowerCase().includes(q) || c.label.toLowerCase().includes(q) || c.description.toLowerCase().includes(q)
      );
      this.selectedSlashIndex = 0;
      this.renderPaletteList(filtered);
    }

    handlePaletteKeydown(e) {
      const items = this.paletteList ? this.paletteList.querySelectorAll(".palette-item") : [];
      if (e.key === "ArrowDown") {
        e.preventDefault();
        this.selectedSlashIndex = Math.min(this.selectedSlashIndex + 1, items.length - 1);
        this.updatePaletteSelection(items);
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        this.selectedSlashIndex = Math.max(this.selectedSlashIndex - 1, 0);
        this.updatePaletteSelection(items);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const selected = items[this.selectedSlashIndex];
        if (selected) {
          selected.click();
        }
      }
    }

    updatePaletteSelection(items) {
      items.forEach((item, idx) => {
        if (idx === this.selectedSlashIndex) {
          item.classList.add("selected");
          item.scrollIntoView({ block: "nearest" });
        } else {
          item.classList.remove("selected");
        }
      });
    }

    applyCommand(cmdText) {
      this.closeSlashPalette();
      if (this.cmdInput) {
        this.cmdInput.value = cmdText + " ";
        this.cmdInput.focus();
        this.cmdInput.setSelectionRange(this.cmdInput.value.length, this.cmdInput.value.length);
      }
    }

    handleQuickAction(action) {
      switch (action) {
        case "code":
          this.applyCommand("/code");
          break;
        case "research":
          this.applyCommand("/research");
          break;
        case "automate":
          this.applyCommand("/automate");
          break;
        case "pc":
          this.applyCommand("/pc");
          break;
        case "more":
          this.openSlashPalette();
          break;
        default:
          break;
      }
    }

    async submitCommand() {
      if (!this.cmdInput || this.isSubmitting) return;
      const text = this.cmdInput.value.trim();
      if (!text) return;

      this.isSubmitting = true;
      if (window.orbitalViz) {
        window.orbitalViz.setState("working");
      }

      try {
        const resp = await fetch("/api/v3/command", {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "X-CSRF-Token": this.csrfToken
          },
          body: JSON.stringify({ text })
        });

        if (resp.ok) {
          this.cmdInput.value = "";
          // Trigger immediate state refresh
          this.pollState();
        } else {
          console.error("Command execution failed", resp.status);
        }
      } catch (err) {
        console.error("Failed to submit command", err);
      } finally {
        this.isSubmitting = false;
        setTimeout(() => {
          if (window.orbitalViz) {
            window.orbitalViz.setState("idle");
          }
        }, 1200);
      }
    }

    toggleVoice() {
      if (window.orbitalViz) {
        const isListening = window.orbitalViz.state === "listen";
        window.orbitalViz.setState(isListening ? "idle" : "listen");
      }
    }

    async sendWindowAction(action) {
      try {
        await fetch(`/api/v3/window/${action}`, {
          method: "POST",
          headers: { "X-CSRF-Token": this.csrfToken }
        });
      } catch (err) {
        console.warn(`Window action ${action} failed`, err);
      }
    }

    startTelemetryLoop() {
      this.pollTelemetry();
      setInterval(() => {
        if (document.visibilityState === "visible") {
          this.pollTelemetry();
        }
      }, 2500);
    }

    async pollTelemetry() {
      try {
        const resp = await fetch("/api/v3/telemetry");
        if (!resp.ok) return;
        const data = await resp.json();

        // 1. CPU
        if (this.cpuVal && this.cpuFill) {
          const cpu = Math.round(data.cpu || 0);
          this.cpuVal.textContent = `${cpu}%`;
          const offset = GAUGE_CIRCUMFERENCE - (cpu / 100) * GAUGE_CIRCUMFERENCE;
          this.cpuFill.style.strokeDashoffset = offset;
        }

        // 2. RAM
        if (this.ramVal && this.ramFill) {
          const ramPct = Math.round(data.ram?.percent || 0);
          this.ramVal.textContent = `${ramPct}%`;
          const offset = GAUGE_CIRCUMFERENCE - (ramPct / 100) * GAUGE_CIRCUMFERENCE;
          this.ramFill.style.strokeDashoffset = offset;
        }

        // 3. GPU
        if (this.gpuVal && this.gpuFill) {
          if (data.gpu && typeof data.gpu.percent === "number") {
            const gpuPct = Math.round(data.gpu.percent);
            this.gpuVal.textContent = `${gpuPct}%`;
            const offset = GAUGE_CIRCUMFERENCE - (gpuPct / 100) * GAUGE_CIRCUMFERENCE;
            this.gpuFill.style.strokeDashoffset = offset;
          } else {
            this.gpuVal.textContent = "N/A";
            this.gpuFill.style.strokeDashoffset = GAUGE_CIRCUMFERENCE;
          }
        }
      } catch (err) {
        // Silently tolerate temporary polling errors
      }
    }

    startStateLoop() {
      this.pollState();
      setInterval(() => {
        if (document.visibilityState === "visible") {
          this.pollState();
        }
      }, 3000);
    }

    async pollState() {
      try {
        const resp = await fetch("/api/v3/state");
        if (!resp.ok) return;
        const data = await resp.json();

        // Update Active Agent
        if (data.active_agent) {
          if (this.agentName) this.agentName.textContent = data.active_agent.name;
          if (this.agentDesc) this.agentDesc.textContent = data.active_agent.description;
          if (this.agentBadge) {
            this.agentBadge.textContent = data.active_agent.status_text || "Ready";
            if (data.active_agent.status === "working") {
              this.agentBadge.style.color = "var(--accent-orange)";
              this.agentBadge.style.backgroundColor = "rgba(255, 146, 43, 0.15)";
            } else {
              this.agentBadge.style.color = "var(--status-green)";
              this.agentBadge.style.backgroundColor = "rgba(52, 211, 153, 0.12)";
            }
          }
        }

        // Update Current Task
        if (data.current_task) {
          if (this.taskTitle) this.taskTitle.textContent = data.current_task.title;
          const pct = Math.round(data.current_task.progress || 100);
          if (this.taskPercent) this.taskPercent.textContent = `${pct}%`;
          if (this.taskFill) this.taskFill.style.width = `${pct}%`;
          if (this.taskDetail) this.taskDetail.textContent = data.current_task.detail || "";
        }
      } catch (err) {
        // Silently tolerate
      }
    }
  }

  // Initialize on DOM load
  document.addEventListener("DOMContentLoaded", () => {
    window.prometheusV3 = new PrometheusV3App();
  });
})();
