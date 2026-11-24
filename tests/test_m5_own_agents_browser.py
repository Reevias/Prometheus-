"""M5 5.4 in a real browser: make, edit and remove an own agent from the Agents page (the form and its buttons)."""
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
    settings = Settings(password_hash=hash_password(PASSWORD), own_agents=True,
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


def test_make_edit_and_remove_an_agent(page, live):
    pg, errors = page
    base, services = live
    pg.goto(base + "/v3/agents?own=new")
    assert pg.locator("input[name=tools]:checked").count() == 0                 # no tool is ticked for a new agent
    pg.fill("#own-form input[name=name]", "Research")                                       # a built-in name: refused with a reason
    pg.fill("#own-form textarea[name=instructions]", "Be brief.")
    pg.click("#own-form button[type=submit]")
    pg.wait_for_selector("#own-error:not([hidden])")
    assert "built-in" in pg.inner_text("#own-error")
    pg.fill("#own-form input[name=name]", "notes_helper")
    pg.fill("#own-form input[name=about]", "Tidies my notes")
    pg.check("input[value=web_search]")
    pg.click("#own-form button[type=submit]")
    pg.wait_for_url("**/v3/agents?show=notes_helper")
    assert [s["tools"] for s in services.settings.own_agent_specs] == [["web_search"]]
    assert "notes_helper" in services.orchestrator.registry
    pg.goto(base + "/v3/agents?own=notes_helper")
    assert pg.locator("input[value=web_search]").is_checked() and pg.locator("#own-form input[name=name]").get_attribute("readonly") is not None
    pg.check("input[value=read_files]")
    pg.click("#own-form button[type=submit]")
    pg.wait_for_url("**/v3/agents?show=notes_helper")
    assert services.settings.own_agent_specs[0]["tools"] == ["web_search", "read_files"]
    pg.goto(base + "/v3/agents")
    pg.click("[data-own-remove=notes_helper]")
    pg.wait_for_function("!document.querySelector('[data-own=notes_helper]')")
    assert services.settings.own_agent_specs == [] and "notes_helper" not in services.orchestrator.registry
    assert errors == []


def test_removing_an_agent_with_a_running_job_asks_first(page, live):
    pg, _ = page
    base, services = live
    pg.goto(base + "/v3/agents?own=new")
    pg.fill("#own-form input[name=name]", "busy_bot")
    pg.fill("#own-form textarea[name=instructions]", "Work.")
    pg.click("#own-form button[type=submit]")
    pg.wait_for_url("**/v3/agents?show=busy_bot")
    job = services.jobs.create_job("graph_step", "busy_bot: long work")
    services.jobs.set_state(job.id, "running")
    asked = []
    pg.once("dialog", lambda d: (asked.append(d.message), d.dismiss()))           # first say no
    pg.goto(base + "/v3/agents")
    pg.click("[data-own-remove=busy_bot]")
    pg.wait_for_timeout(500)
    assert asked and "busy_bot" in asked[0] and "cancel" in asked[0].lower()
    assert "busy_bot" in services.orchestrator.registry and services.jobs.get_job(job.id).state == "running"
    pg.once("dialog", lambda d: d.accept())                                        # then yes
    pg.click("[data-own-remove=busy_bot]")
    pg.wait_for_function("!document.querySelector('[data-own=busy_bot]')")
    assert services.jobs.get_job(job.id).state == "cancelled" and "busy_bot" not in services.orchestrator.registry
