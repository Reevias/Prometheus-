"""M6 6.4b in a real browser: the Devices page makes a pairing code and removes a paired phone."""
from __future__ import annotations

import os
import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
PASSWORD = "correct horse battery"


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def live(tmp_home):
    import uvicorn

    from prometheus.app import build_services
    from prometheus.auth import hash_password
    from prometheus.config import Settings
    from prometheus.models.fake import FakeProvider
    from prometheus.server import create_app

    fake = FakeProvider(lambda m, s: '{"steps": [], "answer": "ok"}', name="fake")
    settings = Settings(password_hash=hash_password(PASSWORD), remote_control=True,
                        routes={"plan": ["fake"], "combine": ["fake"], "research": ["fake"], "private": ["fake"]})
    services = build_services(settings, providers={"fake": fake}, home=tmp_home.home)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(services), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", services
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def page(live):
    pw = sync_api.sync_playwright().start()
    try:
        exe = os.environ.get("PROMETHEUS_CHROMIUM") or ("/opt/pw-browsers/chromium"
                                                         if os.path.exists("/opt/pw-browsers/chromium") else "")
        b = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as e:
        pw.stop()
        pytest.skip(f"Chromium isn't available here: {e}")
    pg = b.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live[0] + "/login")
    pg.fill("input[name=password]", PASSWORD)
    pg.keyboard.press("Enter")
    pg.wait_for_url(lambda u: "/login" not in u)
    yield pg, errors
    b.close()
    pw.stop()


def test_make_a_pairing_code_and_remove_a_phone(page, live):
    pg, errors = page
    base, services = live
    pg.goto(base + "/v3/system?tab=devices")
    assert "No phone is paired yet." in pg.inner_text("#remote-card")
    pg.click("#remote-pair")
    pg.wait_for_selector("#remote-code:not([hidden])")
    code = pg.inner_text("#remote-code").rsplit(": ", 1)[1].strip()
    got = services.remote.pair(code, "Ravi's phone")                     # the phone's side, done in code
    pg.goto(base + "/v3/system?tab=devices")
    assert "Ravi's phone" in pg.inner_text("#remote-card")
    pg.click(f"[data-device='{got['device_id']}'] [data-revoke]")
    pg.wait_for_function("!document.querySelector('[data-device]')")
    assert services.remote.devices() == [] and services.remote.authenticate(got["key"]) is None
    assert errors == []


def test_the_page_works_on_a_phone_width(page, live):
    pg, _ = page
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(live[0] + "/v3/system?tab=devices")
    pg.wait_for_selector("#remote-pair")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
