"""M6 step 6.5: Phone control through mobile-mcp (through Other apps), credits, approvals, and tests."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


# -- Item 2: mobile-mcp via Other apps & Credits -------------------------------------------------------------------
def test_item2_credits_list_mobile_mcp_with_apache2_licence():
    root = Path(__file__).resolve().parent.parent
    settings_html = (root / "prometheus" / "web" / "templates" / "v3" / "settings.html").read_text(encoding="utf-8")
    matrix_md = (root / "docs" / "m0" / "DEPENDENCY_MATRIX.md").read_text(encoding="utf-8")

    assert "mobile-mcp" in settings_html and "Apache-2.0" in settings_html
    assert "mobile-mcp" in matrix_md and "1.0.20" in matrix_md and "Apache-2.0" in matrix_md


def test_item2_connector_manager_provides_mobile_mcp_in_other_apps(app_client):
    from prometheus.connectors.manager import ConnectionManager, ensure_mobile_mcp, find_mobile_mcp

    settings = SimpleNamespace(mcp_servers=[])
    entry = ensure_mobile_mcp(settings)
    assert entry["name"] == "mobile-mcp"
    assert entry["kind"] == "command"
    assert "@mobilenext/mobile-mcp" in entry["target"]
    assert entry["enabled"] is True
    assert settings.mcp_servers == [entry]

    class FakeServer:
        def __init__(self, tools):
            self.tools = tools

        def request(self, method, params=None, timeout=120):
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {"tools": self.tools}
            return {"content": [{"type": "text", "text": "ok"}]}

        def notify(self, method, params=None):
            pass

        def close(self):
            pass

    mgr = ConnectionManager(
        settings,
        factory=lambda e: FakeServer([{"name": "mobile_list_available_devices", "description": "List devices"}]),
    )
    conn = find_mobile_mcp(mgr)
    assert conn.name == "mobile-mcp"

    client, services = app_client()
    from tests.conftest import login

    h = {"X-CSRF-Token": login(client)}
    r = client.post("/settings/phone/mobile-mcp", headers=h)
    assert r.status_code == 200
    assert any(e.get("name") == "mobile-mcp" for e in services.settings.mcp_servers)


def test_item2_mcp_connection_call_raw_preserves_image_bytes():
    from prometheus.connectors.mcp_client import Connection

    png_bytes = b"\x89PNG\r\n\x1a\nFAKE"

    class ImgServer:
        def request(self, method, params=None, timeout=120):
            if method == "initialize":
                return {}
            if method == "tools/list":
                return {"tools": [{"name": "mobile_take_screenshot", "annotations": {"readOnlyHint": True}}]}
            return {
                "content": [
                    {"type": "text", "text": "Screenshot captured"},
                    {"type": "image", "mimeType": "image/png", "data": base64.b64encode(png_bytes).decode()},
                ]
            }

        def notify(self, method, params=None):
            pass

        def close(self):
            pass

    conn = Connection("mobile-mcp", ImgServer())
    conn.start()
    raw = conn.call_raw("mobile_take_screenshot", {"device": "dev1"})
    img_item = [c for c in raw["content"] if c.get("type") == "image"][0]
    assert base64.b64decode(img_item["data"]) == png_bytes
