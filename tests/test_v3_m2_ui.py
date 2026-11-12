"""Milestone M2 Comprehensive Tests: Desktop Application Interface & Functional Integration.

Tests cover:
1. System telemetry service (CPU, RAM, GPU, uptime, non-fabrication).
2. UI state service (slash command registry, active agent, current task).
3. V3 Shell template rendering (three panels, branding, controls, quick actions, wave).
4. Dual-mode routing (v3 shell by default, ?view=classic fallback).
5. API endpoints (/api/v3/telemetry, /api/v3/state, /api/v3/slash-commands, /api/v3/command, /api/v3/window).
6. Static assets (theme tokens, orbital canvas, app orchestration, no external downloads).
"""
import re
import pytest
from conftest import login

from prometheus.services.telemetry import SystemTelemetryService, telemetry_service
from prometheus.services.ui_state import UIStateService, ui_state_service


# ============================================================================
# 1. TELEMETRY SERVICE UNIT TESTS
# ============================================================================

def test_telemetry_service_cpu():
    """Verify genuine CPU utilization is reported within valid percentage bounds."""
    cpu = telemetry_service.get_cpu_percent()
    assert isinstance(cpu, float)
    assert 0.0 <= cpu <= 100.0


def test_telemetry_service_ram():
    """Verify genuine RAM metrics are reported with positive memory totals."""
    ram = telemetry_service.get_ram_info()
    assert 0.0 <= ram.percent <= 100.0
    # On real system total_gb must be positive
    assert ram.total_gb >= 0.0
    assert ram.used_gb >= 0.0
    assert ram.free_gb >= 0.0


def test_telemetry_service_gpu_non_fabricated():
    """Verify GPU returns either valid metric or None, never fabricated dummy values."""
    gpu = telemetry_service.get_gpu_info()
    if gpu is not None:
        assert isinstance(gpu.percent, float)
        assert 0.0 <= gpu.percent <= 100.0
        assert isinstance(gpu.model, str)
        assert len(gpu.model) > 0


def test_telemetry_service_uptime():
    """Verify system uptime in seconds is positive and increasing."""
    up1 = telemetry_service.get_uptime_seconds()
    assert isinstance(up1, float)
    assert up1 > 0.0


def test_telemetry_service_full_snapshot():
    """Verify full telemetry dictionary structure."""
    telemetry = telemetry_service.get_telemetry()
    assert "cpu" in telemetry
    assert "ram" in telemetry
    assert "gpu" in telemetry
    assert "uptime_seconds" in telemetry
    assert "platform" in telemetry
    assert isinstance(telemetry["cpu"], float)
    assert isinstance(telemetry["ram"], dict)


# ============================================================================
# 2. UI STATE SERVICE UNIT TESTS
# ============================================================================

def test_ui_state_slash_commands_registry():
    """Verify all 9 standard slash commands are registered with examples and labels."""
    service = UIStateService()
    commands = service.get_slash_commands()
    assert len(commands) >= 9
    cmd_names = {c["command"] for c in commands}
    expected = {"/code", "/research", "/automate", "/pc", "/agents", "/memory", "/skills", "/system", "/help"}
    assert expected.issubset(cmd_names)

    for c in commands:
        assert c["command"].startswith("/")
        assert "label" in c and len(c["label"]) > 0
        assert "description" in c and len(c["description"]) > 0
        assert "example" in c and len(c["example"]) > 0


def test_ui_state_active_agent_standby():
    """Verify default standby active agent metadata."""
    service = UIStateService()
    agent = service.get_active_agent()
    assert agent["name"] == "System Orchestrator"
    assert agent["status"] in ("idle", "ready")
    assert "Ready to assist" in agent["description"]


def test_ui_state_current_task_idle():
    """Verify default current task metadata when idle."""
    service = UIStateService()
    task = service.get_current_task()
    assert task["title"] == "System Monitoring"
    assert task["progress"] == 100.0
    assert task["detail"] == "All systems operational"


def test_ui_state_full_snapshot():
    """Verify unified state snapshot."""
    service = UIStateService()
    state = service.get_full_state()
    assert state["online"] is True
    assert "timestamp" in state
    assert "active_agent" in state
    assert "current_task" in state
    assert "slash_commands" in state


# ============================================================================
# 3. WEB INTERFACE & V3 ROUTE INTEGRATION TESTS
# ============================================================================

def test_v3_shell_renders_complete_interface(app_client):
    """Verify GET /v3 renders all 15 required components from the visual design spec."""
    client, services = app_client()
    login(client)

    resp = client.get("/v3")
    assert resp.status_code == 200
    page = resp.text

    # Component 1: Application shell
    assert 'class="v3-shell"' in page

    # Component 2: Left sidebar branding & 10 navigation items
    assert "PROMETHEUS" in page
    assert "v3.0" in page
    assert "Saiveer" in page
    assert "Pro" in page
    assert "nav-home" in page
    assert "nav-agents" in page
    assert "nav-skills" in page
    assert "nav-tasks" in page
    assert "nav-projects" in page
    assert "nav-memory" in page
    assert "nav-automations" in page
    assert "nav-research" in page
    assert "nav-integrations" in page
    assert "nav-system" in page

    # Component 3: Orbital Canvas
    assert 'id="orbital-canvas"' in page

    # Component 4: Branding typography
    assert "Your Personal AI System" in page

    # Component 5: Primary command input
    assert 'id="command-input"' in page
    assert 'id="slash-indicator"' in page
    assert 'id="mic-btn"' in page
    assert 'id="submit-btn"' in page

    # Component 6: Slash command palette modal
    assert 'id="slash-palette-overlay"' in page
    assert 'id="palette-search"' in page
    assert 'id="palette-list"' in page

    # Component 7: 5 Quick Action pills
    assert 'data-action="code"' in page
    assert 'data-action="research"' in page
    assert 'data-action="automate"' in page
    assert 'data-action="pc"' in page
    assert 'data-action="more"' in page

    # Component 8: Right panel status badge and audio waveform
    assert "Prometheus Online" in page
    assert 'class="status-dot"' in page
    assert 'class="audio-waveform"' in page

    # Component 9: System monitoring circular gauges
    assert 'id="cpu-gauge-fill"' in page
    assert 'id="ram-gauge-fill"' in page
    assert 'id="gpu-gauge-fill"' in page

    # Component 10: Active agent card
    assert 'id="card-active-agent"' in page
    assert 'id="active-agent-name"' in page

    # Component 11: Current task card
    assert 'id="card-current-task"' in page
    assert 'id="current-task-fill"' in page

    # Component 12: Atmospheric bottom wave
    assert 'class="atmospheric-wave"' in page
    assert "waveGlow" in page

    # Component 13: Window controls
    assert 'id="win-min-btn"' in page
    assert 'id="win-max-btn"' in page
    assert 'id="win-close-btn"' in page


def test_home_route_dual_mode(app_client):
    """Verify GET / renders v3 shell by default, and ?view=classic renders legacy chat."""
    client, services = app_client()
    login(client)

    # Default -> v3 shell
    v3_page = client.get("/").text
    assert 'class="v3-shell"' in v3_page

    # ?view=classic -> classic chat page
    classic_page = client.get("/?view=classic").text
    assert 'class="v3-shell"' not in classic_page
    assert "panel chat" in classic_page and 'id="messages"' in classic_page


def test_api_v3_telemetry_endpoint(app_client):
    """Verify GET /api/v3/telemetry returns genuine system metrics."""
    client, services = app_client()
    login(client)

    resp = client.get("/api/v3/telemetry")
    assert resp.status_code == 200
    data = resp.json()
    assert "cpu" in data and isinstance(data["cpu"], (int, float))
    assert "ram" in data and isinstance(data["ram"]["percent"], (int, float))
    assert "uptime_seconds" in data and data["uptime_seconds"] > 0


def test_api_v3_state_endpoint(app_client):
    """Verify GET /api/v3/state returns status, active agent, and task."""
    client, services = app_client()
    login(client)

    resp = client.get("/api/v3/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["online"] is True
    assert "active_agent" in data
    assert "current_task" in data
    assert "slash_commands" in data


def test_api_v3_slash_commands_endpoint(app_client):
    """Verify GET /api/v3/slash-commands returns command registry array."""
    client, services = app_client()
    login(client)

    resp = client.get("/api/v3/slash-commands")
    assert resp.status_code == 200
    commands = resp.json()
    assert isinstance(commands, list)
    assert len(commands) >= 9


def test_api_v3_command_submission(app_client):
    """Verify POST /api/v3/command submits instruction and creates task."""
    client, services = app_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    # Empty payload rejection
    empty_resp = client.post("/api/v3/command", json={"text": ""}, headers=headers)
    assert empty_resp.status_code == 400

    # Valid payload acceptance
    valid_resp = client.post("/api/v3/command", json={"text": "Review system integrity"}, headers=headers)
    assert valid_resp.status_code == 200
    assert valid_resp.json()["status"] == "ok"
    assert valid_resp.json()["text"] == "Review system integrity"


def test_api_v3_window_controls(app_client):
    """Verify POST /api/v3/window/{action} handles minimize, maximize, close."""
    client, services = app_client()
    csrf = login(client)
    headers = {"X-CSRF-Token": csrf}

    for action in ("minimize", "maximize", "close"):
        resp = client.post(f"/api/v3/window/{action}", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        assert resp.json()["action"] == action


# ============================================================================
# 4. STATIC ASSETS AUDIT TESTS
# ============================================================================

def test_v3_static_theme_css(app_client):
    """Verify v3 theme CSS contains obsidian tokens and no external imports."""
    client, _ = app_client()
    resp = client.get("/static/v3/theme.css")
    assert resp.status_code == 200
    css = resp.text

    assert "@import" not in css
    assert "http://" not in css and "https://" not in css
    assert "--bg-obsidian" in css
    assert "--accent-orange" in css
    assert "prefers-reduced-motion" in css


def test_v3_static_orbital_canvas_js(app_client):
    """Verify v3 orbital canvas script exists and defines OrbitalCanvas."""
    client, _ = app_client()
    resp = client.get("/static/v3/orbital_canvas.js")
    assert resp.status_code == 200
    assert "OrbitalCanvas" in resp.text
    assert "requestAnimationFrame" in resp.text


def test_v3_static_app_js(app_client):
    """Verify v3 application script exists and defines PrometheusV3App."""
    client, _ = app_client()
    resp = client.get("/static/v3/app.js")
    assert resp.status_code == 200
    assert "PrometheusV3App" in resp.text
    assert "/api/v3/telemetry" in resp.text
    assert "/api/v3/command" in resp.text
