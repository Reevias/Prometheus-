# PROMETHEUS 3.0 — MILESTONE 2 (M2) KNOWN LIMITATIONS & SCOPE BOUNDARIES

This document outlines the architectural boundaries, environmental limitations, and intentional design trade-offs in Milestone 2 (M2).

---

## 1. Hardware Telemetry & Non-Fabrication Boundaries

### 1.1 Integrated GPUs vs. Dedicated NVIDIA Hardware
- **Current Behavior**: The `SystemTelemetryService` queries NVIDIA GPU metrics via `nvidia-smi`. On machines with integrated GPUs (such as Intel UHD Graphics or AMD APUs without dedicated GPU drivers), `nvidia-smi` is unavailable.
- **Architectural Decision**: Under the strict Prometheus integrity guidelines, the service deliberately refrains from generating synthetic or simulated GPU percentages. It returns `None`, and the UI cleanly renders `"N/A"` with a zeroed radial progress arc.
- **Future Pathway (M4+)**: Integrate DirectX/DXGI performance counters or Windows D3D device statistics to query integrated GPU engine load on Windows.

### 1.2 CPU Sampling Resolution
- **Current Behavior**: CPU utilization is queried via Windows `kernel32.GetSystemTimes` by comparing tick deltas across requests. If requests arrive faster than 150ms apart, the cached calculation is returned to prevent CPU thrashing or sampling jitter.
- **Impact**: Extremely lightweight (<0.05ms per query), zero C-extension overhead, but not intended for microsecond-level profiling.

---

## 2. Desktop Window Control Boundaries

### 2.1 Pure Browser vs. PyWebView Runtime
- **Current Behavior**: The window controls (Minimize, Maximize, Close) in the top right of the application panel dispatch `POST /api/v3/window/{action}` requests to the backend.
  - When running inside the official desktop wrapper (`prometheus desktop` via PyWebView/WebView2), the backend invokes `window.minimize()`, `window.maximize()`, and `window.destroy()`.
  - When accessed via an independent browser tab (e.g. `http://127.0.0.1:8000/`), modern browser security sandboxes prevent web applications from minimizing or terminating the host browser process. The backend returns `{"status": "ok", "action": action}`, but the browser window remains open.
- **Impact**: Expected behavior conforming to W3C browser security specifications.

---

## 3. Audio & Voice Interaction Boundaries

### 3.1 Local Voice Dependencies
- **Current Behavior**: Clicking the microphone button toggles the listening state in the orbital canvas (`listen` mode) and visual waveform.
- **Requirement**: Full offline voice transcription requires the optional voice package (`pip install ".[voice]"` covering Vosk/Kokoro/Faster-Whisper). In environments where voice dependencies are not compiled, the interface provides visual state indication without background speech-to-text pipeline activation.

---

## 4. Milestone Boundaries & Non-Goals

### 4.1 No Premature Milestone 3 Work
- **Boundary**: Milestone M2 is exclusively focused on the desktop interface, visual fidelity to the reference screenshot, and functional state integration.
- **Explicit Non-Goals**:
  - No new ReAct DAG workflow engines.
  - No autonomous multi-agent tool loops.
  - No dynamic model router rewrites.
- All command submissions flow cleanly into the existing Prometheus v2 task execution pipeline without breaking interfaces.
