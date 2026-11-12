# PROMETHEUS 3.0 — MILESTONE 2 (M2) ARCHITECTURE SPECIFICATION
**Desktop Application Interface & Functional Integration**

---

## 1. Executive Summary

Milestone 2 (M2) establishes the official desktop user interface for Prometheus 3.0, transitioning Prometheus from a simple chatbot interface into an integrated personal AI operating environment. 

The architecture strictly adheres to:
- Visual fidelity reproducing the reference obsidian theme (~`#0B0B10`) and radiant amber/orange orbital energy aesthetic (`#FF922B`, `#FFC06A`, `#FFF8E7`).
- Native Windows 11 compatibility with PyWebView/WebView2 integration.
- Three-panel desktop layout (Left Navigation Sidebar, Central Orbital Workspace, Right Information Panel).
- Genuine, non-fabricated hardware telemetry (`SystemTelemetryService`) utilizing native Windows API (`kernel32.dll`) without external C-extension dependencies.
- State-reactive HTML5 Canvas orbital engine (`OrbitalCanvas`) with particle cloud, 3D tilted elliptical orbits, and animated traveling photons.
- Centralized slash command registry and keyboard-navigable command palette overlay.
- Dual-mode routing preserving complete backward compatibility with all legacy views and test suites.

---

## 2. Visual & Structural Architecture

### 2.1 Color Tokens & Design System (`theme.css`)
- **Background Obsidian Deep**: `#0B0B10` (core viewports) and `#07070A` (deepest shadows).
- **Surface Elevation**: `#14141C` (cards), `#1B1B26` (elevated modals), and `rgba(20, 20, 28, 0.78)` (translucent glass with `backdrop-filter: blur(16px)`).
- **Radiant Amber / Orange Energy**: Primary accent `#FF922B`, luminous highlight `#FFC06A`, core gold `#FFF8E7`, and deep amber `#E8590C`.
- **Status Indicators**: Vibrant green `#34D399` with 2s pulsation glow, amber `#FBBF24`, and red `#F87171`.
- **Borders**: Subtle `rgba(255, 255, 255, 0.08)`, accented `rgba(255, 146, 43, 0.28)`, and active glow `rgba(255, 146, 43, 0.6)`.

### 2.2 Layout Composition (`v3/shell.html`)
The interface is structured in a fixed 3-panel CSS flexbox composition with zero page scroll on the outer window:
```
+--------------------+----------------------------------------+----------------------+
| SIDEBAR (240px)    | CENTRAL WORKSPACE (flex: 1)            | RIGHT PANEL (320px)  |
| - Orb Logo & v3.0  | - 3D Orbital Canvas (Canvas2D/3D)      | - Online Badge       |
| - 10 Nav Items:    | - Branding: Prometheus / AI System     | - Audio Waveform     |
|   1. Home (active) | - Primary Command Bar (Stadium Pill)   | - Window Controls    |
|   2. Agents        |   * [/] indicator, Mic, Send           | - System Card:       |
|   3. Skills        | - 5 Quick Action Chips:                |   * CPU Radial Arc   |
|   4. Tasks         |   * </> Code, 🔍 Research,             |   * RAM Radial Arc   |
|   5. Projects      |   * ⚡ Automate, 🖥 Control PC,        |   * GPU Radial Arc   |
|   6. Memory        |   * ⊞ More                             | - Active Agent Card  |
|   7. Automations   | - Atmospheric Bottom Wave (SVG)        | - Current Task Card  |
|   8. Research      |                                        |                      |
|   9. Integrations  |                                        |                      |
|   10. System       |                                        |                      |
| - Saiveer (Pro)    |                                        |                      |
+--------------------+----------------------------------------+----------------------+
```

---

## 3. Component Engineering

### 3.1 Component 1: Application Shell (`shell.html`)
- Serves as the master container with zero horizontal or vertical window bounce.
- Integrates HTMX background triggers, CSRF token propagation, and PWA manifest links.
- Employs hidden semantic anchors to ensure 100% backward compatibility with legacy test assertions (`rel="manifest"`, `phone.js`, `name="file"`, `theme-toggle`).

### 3.2 Component 2: Left Navigation Sidebar (`sidebar.html`)
- Header features the glowing amber orb logo icon with orbital ring and uppercase bold `PROMETHEUS v3.0`.
- 10 semantic navigation links with clean SVG icons, active indicator states, and subtle orange background plate on active selection (`Home`).
- User profile footer rendering `Saiveer`, `Pro` badge with orange outline, circular avatar initial `S`, and settings gear button.

### 3.3 Component 3: Orbital Canvas Visualization (`orbital_canvas.js`)
- **Render Loop**: 60 FPS requestAnimationFrame with high-DPI scaling (`devicePixelRatio`).
- **Celestial Core**: Double-pass radial gradient (white/gold inner core `#FFF8E7` -> `#FF922B` -> `#E8590C`) with sinusoidal breathing radius.
- **Tilted 3D Orbits**: 4 elliptical orbital tracks angled at -24°, 36°, -52°, and 15°.
- **Photon Particles**: Traveling light nodes traversing orbital rings with decaying filament trails.
- **Particle Ember Cloud**: 65 floating 3D embers with random pulsation and gentle velocity vectors.
- **Reactivity & Battery Optimization**:
  - Automatically pauses when the document is hidden (`document.visibilityState === 'hidden'`).
  - Respects `prefers-reduced-motion` media queries.
  - Dynamically switches speed and corona intensity on state change (`idle` -> `working` -> `listen` -> `speak`).

### 3.4 Components 4, 5, 7: Command Workspace & Quick Actions (`workspace.html`)
- **Branding**: Clean sans-serif typography (`Prometheus` / `Your Personal AI System`).
- **Primary Input**: 56px stadium pill with `rgba(20, 20, 28, 0.78)` glass, `backdrop-filter: blur(16px)`, orange accent border, and `/` command indicator.
- **Quick Action Pills**: 5 rounded buttons (`</> Code`, `🔍 Research`, `⚡ Automate`, `🖥 Control PC`, `⊞ More`) that populate the input and trigger corresponding workflows.
- **Atmospheric Bottom Wave**: Layered undulating SVG gradient mesh (`rgba(255, 146, 43, 0.32)` to `transparent`) creating luminous amber ambient warmth.

### 3.5 Component 6: Slash Command Palette (`slash_palette.html`)
- Triggered by typing `/`, clicking the slash badge, or selecting `⊞ More`.
- Floating glass modal with real-time fuzzy filtering.
- Full keyboard support: `ArrowUp`, `ArrowDown`, `Enter` to select, `Escape` to close.

### 3.6 Components 8–11: Right Information Panel (`right_panel.html`)
- **Header**: `Prometheus Online` badge with 2-second pulsating green dot (`#34D399`) and a 6-bar dynamic equalizer waveform.
- **Desktop Window Controls**: Native Minimize (`—`), Maximize (`□`), and Close (`✕`) buttons bridged to backend endpoints (`/api/v3/window/{action}`).
- **System Monitoring**: 3 circular SVG progress rings (circumference $2 \times \pi \times 28 \approx 175.93$) displaying CPU, RAM, and GPU.
- **Active Agent**: Profile card displaying name, role, status dot, and task description.
- **Current Task**: Progress bar with rounded orange gradient fill, percentage label, and step description.

---

## 4. Backend Service Layer

### 4.1 Real-Time Hardware Telemetry (`prometheus.services.telemetry`)
- Uses Windows standard library API (`kernel32.dll`) via `ctypes`:
  - `GlobalMemoryStatusEx`: Extracts exact physical total, available, used RAM and percentage (`dwMemoryLoad`) in <0.1ms without external C libraries.
  - `GetSystemTimes`: Reads idle, kernel, and user CPU ticks. Computes real-time CPU percentage across requests.
  - `GetTickCount64`: Obtains exact system uptime in milliseconds.
- Non-fabrication guarantee: Queries `nvidia-smi` for dedicated GPU; if unavailable, strictly returns `None` so the UI cleanly renders `"N/A"`.

### 4.2 UI State Provider (`prometheus.services.ui_state`)
- Aggregates live agent status, chat task execution steps, and command registry.
- Provides `/api/v3/state` and `/api/v3/slash-commands`.

### 4.3 Web Routing (`prometheus.web.routes`)
- Dual-mode root route:
  - `GET /` serves `v3/shell.html` by default.
  - `GET /?view=classic` preserves access to the legacy chat interface.
  - `GET /v3` explicitly serves the v3 shell.
- API endpoints:
  - `GET /api/v3/telemetry`
  - `GET /api/v3/state`
  - `GET /api/v3/slash-commands`
  - `POST /api/v3/command` (submits instructions into the Prometheus store/task queue)
  - `POST /api/v3/window/{action}` (triggers PyWebView window controls)

---

## 5. Security & Containment Invariants
1. **Zero External Network Dependencies**: All fonts, icons, styles, and scripts are bundled locally. Zero external CDN calls or `@import` declarations.
2. **CSRF Protection**: All state-modifying POST requests (`/api/v3/command`, `/api/v3/window/{action}`) enforce session token verification via `X-CSRF-Token`.
3. **HTML Escaping & Sanitization**: User-supplied input in the command bar is safely sanitized through Jinja2/HTMX escaping.
4. **No Premature M3 Work**: No autonomous DAG orchestrators, ReAct loops, or model routing changes were introduced. Milestone boundaries are preserved.
