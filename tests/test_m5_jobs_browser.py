"""M5 5.5 in a real browser: every job button, a cancelled and a failed job, the Canvas diagram and the phone layout.

Needs Playwright with Chromium (the GitHub "Click through" job has it on Windows; elsewhere skipped without one)."""
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
    from prometheus.jobs.graph import Slots
    from prometheus.models.fake import FakeProvider
    from prometheus.server import create_app

    fake = FakeProvider(lambda m, s: '{"steps": [], "answer": "ok"}', name="fake")
    settings = Settings(password_hash=hash_password(PASSWORD), routes={"plan": ["fake"], "combine": ["fake"],
                                                                     "research": ["fake"], "private": ["fake"]})
    services = build_services(settings, providers={"fake": fake}, home=tmp_home.home)
    gate = threading.Event()
    services.graphs.slots = Slots(2)
    gid = services.graphs.create("Compare two laptops", [
        {"id": "a", "title": "research: laptop A", "func": lambda c, i: gate.wait(20) and None, "retries": 0},
        {"id": "b", "title": "research: laptop B", "func": lambda c, i: gate.wait(20) and None, "retries": 0},
        {"id": "c", "title": "summarise: compare", "func": lambda c, i: "ok", "needs": ["a", "b"], "retries": 0}])
    services.graphs.start(gid)
    deadline = time.time() + 5
    while time.time() < deadline and services.jobs.get_job(gid).state != "running":
        time.sleep(0.02)
    failed = services.jobs.create_job("work", "Convert the video")
    services.jobs.set_state(failed.id, "running")
    services.jobs.set_state(failed.id, "failed", error="ffmpeg is not installed")
    cancelled = services.jobs.create_job("work", "Index the old photos")
    services.jobs.set_state(cancelled.id, "cancelled", error="Cancelled by you")
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(services), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", services, gid
    gate.set()
    services.graphs.cancel(gid, "test over")
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
    ctx = b.new_context(viewport={"width": 1280, "height": 900})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(live[0] + "/login")
    pg.fill("input[name=password]", PASSWORD)
    pg.keyboard.press("Enter")
    pg.wait_for_url(lambda u: "/login" not in u)
    pg.goto(live[0] + "/v3/agents")
    yield pg, errors
    b.close()
    pw.stop()


def state_of(pg, gid):
    return pg.locator(f'[data-job="{gid}"] .job-state').inner_text().strip()


def test_pause_resume_and_stop_buttons_work_in_the_browser(page, live):
    pg, errors = page
    gid = live[2]
    assert state_of(pg, gid) == "running"
    assert pg.locator(f'[data-job="{gid}"] .job-resume').count() == 0           # not allowed while it runs
    pg.click(f'[data-job="{gid}"] .job-pause')
    pg.wait_for_selector(f'[data-job="{gid}"] .job-state.st-paused')
    assert live[1].jobs.get_job(gid).state == "paused"
    assert pg.locator(f'[data-job="{gid}"] .job-pause').count() == 0
    pg.click(f'[data-job="{gid}"] .job-resume')
    pg.wait_for_function("id => !document.querySelector(`[data-job='${id}'] .job-state.st-paused`)", arg=gid)
    assert live[1].jobs.get_job(gid).state in ("pending", "running")
    pg.once("dialog", lambda d: d.accept())
    pg.click(f'[data-job="{gid}"] .job-stop')
    pg.wait_for_selector(f'[data-job="{gid}"] .job-state.st-cancelled')
    assert live[1].jobs.get_job(gid).state == "cancelled"
    assert pg.locator(f'[data-job="{gid}"] .job-btn').count() == 0              # finished: no buttons left
    assert errors == []


def test_a_failed_and_a_cancelled_job_show_why_and_have_no_buttons(page, live):
    pg, _ = page
    failed = pg.locator("[data-job]", has_text="Convert the video")
    assert "failed" in failed.inner_text() and "ffmpeg is not installed" in failed.inner_text()
    assert failed.locator("button").count() == 0
    cancelled = pg.locator("[data-job]", has_text="Index the old photos")
    assert "cancelled" in cancelled.inner_text() and "Cancelled by you" in cancelled.inner_text()
    assert cancelled.locator("button").count() == 0


def test_the_canvas_diagram_is_drawn_in_the_palette_colours(page, live):
    pg, _ = page
    pg.wait_for_selector("canvas.job-graph")
    info = pg.evaluate("""() => { const c = document.querySelector('canvas.job-graph');
        const x = c.getContext('2d'), d = x.getImageData(0, 0, c.width, c.height).data;
        const seen = new Set(); for (let i = 0; i < d.length; i += 4) if (d[i + 3] > 200) seen.add(d[i] + ',' + d[i+1] + ',' + d[i+2]);
        return {w: c.width, h: c.height, colours: [...seen]}; }""")
    assert info["w"] > 0 and info["h"] > 0
    assert "255,146,43" in info["colours"]                    # running steps in #FF922B
    assert any(c.startswith("244,241,237") for c in info["colours"])   # labels in #F4F1ED


def test_the_view_updates_by_itself(page, live):
    pg, _ = page
    late = live[1].jobs.create_job("work", "Added while the page was open")
    live[1].jobs.set_state(late.id, "running")
    pg.wait_for_selector("text=Added while the page was open", timeout=9000)


def test_the_phone_layout_does_not_overflow(page, live):
    pg, _ = page
    pg.set_viewport_size({"width": 390, "height": 800})
    pg.goto(live[0] + "/v3/agents")
    pg.wait_for_selector(".job-row")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    assert pg.locator(f'[data-job="{live[2]}"] .job-pause').is_visible()
