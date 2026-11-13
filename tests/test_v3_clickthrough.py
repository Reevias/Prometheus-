"""M2: click every visible control of the new interface in a real browser. A control that does nothing (no change
on the page, no request, no navigation, no question) fails the test, and so does any page error or server error.

Needs Playwright with Chromium (pip install -e ".[browser]"; playwright install chromium). The GitHub "Click
through" job runs it on Windows; elsewhere it is skipped when the browser isn't available.
Buttons that destroy or stop things (Delete, Stop all, Log out, the window buttons) are checked for being wired up,
never clicked.
"""
from __future__ import annotations

import socket
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PASSWORD = "correct horse battery"
SKIP = ('[data-win]', '.stopall', '[data-act="delete"]', '[hx-post="/logout"]', '[hx-post*="/delete"]',
        '#lightbox-close', '.danger')
PROBE = """
() => {
  window.__events = 0;
  new MutationObserver(m => { window.__events += m.length; }).observe(document.documentElement,
      {subtree: true, childList: true, attributes: true, characterData: true});
}
"""


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

    fake = FakeProvider(lambda m, s: '{"steps": [], "answer": "Here is an **answer** with `code`."}', name="fake")
    settings = Settings(password_hash=hash_password(PASSWORD),
                        routes={"plan": ["fake"], "combine": ["fake"], "research": ["fake"], "private": ["fake"]})
    services = build_services(settings, providers={"fake": fake}, home=tmp_home.home)
    st = services.store
    chat = st.create_chat("Example chat")
    st.add_message(chat, "user", "show me code")
    tid = st.create_task(chat, "show me code")
    sid = st.add_step(tid, "code", "Write an example", {"language": "python"})
    st.finish_step(sid, "done", "Wrote it")
    st.set_task_status(tid, "done")
    st.add_message(chat, "assistant", "Here:\n\n```python\nprint(1)\n```\n\n| a | b |\n|---|---|\n| 1 | 2 |", "fake")
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(services), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", chat
    server.should_exit = True
    thread.join(5)


@pytest.fixture
def browser():
    import os
    pw = sync_api.sync_playwright().start()
    try:
        exe = os.environ.get("PROMETHEUS_CHROMIUM")          # a browser already on this machine, if Playwright's isn't
        b = pw.chromium.launch(executable_path=exe) if exe else pw.chromium.launch()
    except Exception as e:                       # no browser on this machine: the CI job installs one
        pw.stop()                                # always: a running Playwright loop breaks later asyncio tests
        pytest.skip(f"Chromium isn't available here: {e}")
    try:
        yield b
    finally:
        b.close()
        pw.stop()


def visible_controls(page):
    sel = "button:visible, [role=menuitem]:visible, summary:visible, a.chip:visible"
    out = []
    for i, el in enumerate(page.query_selector_all(sel)):
        if el.is_disabled():
            continue
        if any(el.evaluate(f"e => e.matches({s!r}) || !!e.closest({s!r})") for s in SKIP):
            continue
        label = (el.get_attribute("aria-label") or el.get_attribute("title") or el.inner_text() or "").strip()[:40]
        out.append((i, label))
    return sel, out


def click_all(page, url, problems, seen):
    page.goto(url)
    page.wait_for_load_state("networkidle")
    sel, controls = visible_controls(page)
    last = None
    for index, label in controls:
        page.goto(url)
        page.wait_for_load_state("networkidle")
        if "/login" in page.url:
            problems.append(f"{url}: signed out after clicking '{last}'")
            return
        last = label
        page.evaluate(PROBE)
        els = page.query_selector_all(sel)
        if index >= len(els) or not els[index].is_visible():
            continue
        seen["requests"] = seen["asked"] = 0
        before = page.url
        try:
            els[index].click(timeout=3000)
        except Exception as e:
            problems.append(f"{url}: couldn't click '{label}': {str(e)[:80]}")
            continue
        page.wait_for_timeout(350)
        if "/login" in page.url:
            problems.append(f"{url}: '{label}' signed the owner out")
            return
        changed = page.evaluate("window.__events || 0") if page.url == before else 1
        if not (changed or seen["requests"] or seen["asked"] or page.url != before):
            problems.append(f"{url}: '{label}' did nothing")
        page.keyboard.press("Escape")


def test_every_control_does_something_and_nothing_breaks(live, browser):
    base, chat = live
    page = browser.new_page(viewport={"width": 1600, "height": 900})
    # every load starts from the default layout: a click that shrinks the menu (remembered in the browser) would
    # otherwise move the buttons, and the next numbered click would land on a different one
    page.add_init_script("try { localStorage.clear(); } catch (e) {}")
    # the clipboard refuses, as Windows' browser did on GitHub: Copy must say so, never throw an error
    page.add_init_script("try { navigator.clipboard.writeText = function () {"
                         " return Promise.reject(new DOMException('Write permission denied.', 'NotAllowedError')); };"
                         " } catch (e) {}")
    errors, server_errors, problems = [], [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    seen = {"requests": 0, "asked": 0}                        # one listener each for the whole test
    page.on("request", lambda r: seen.__setitem__("requests", seen["requests"] + 1))
    page.on("dialog", lambda d: (seen.__setitem__("asked", seen["asked"] + 1), d.dismiss()))
    page.on("response", lambda r: server_errors.append(f"{r.status} {r.url}") if r.status >= 500 else None)
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    for path in ("/", "/v3/chats", f"/c/{chat}", "/v3/agents", "/v3/agents?show=code"):
        click_all(page, base + path, problems, seen)

    # Settings: the category menu leads to a real section every time (its forms are tested elsewhere)
    page.goto(base + "/settings")
    for link in page.query_selector_all(".set-nav a:visible"):
        target = link.get_attribute("href")
        assert target.startswith("#") and page.query_selector(target), target
    # every page in the sidebar and in More opens without an error
    page.goto(base + "/")
    page.click(".nav-more summary")
    links = [a.get_attribute("href") for a in page.query_selector_all(".sidebar a[href]")]
    sections = {h.split("#", 1)[1] for h in links if "#" in h}       # your menu links to sections of Settings
    for href in dict.fromkeys(h.split("#", 1)[0] for h in links):    # each page once (a #section isn't a new load)
        r = page.goto(base + href)
        assert r is not None and r.status < 400, href
    page.goto(base + "/settings")
    for section in sections:
        assert page.query_selector("#" + section), section
    # the dangerous buttons are wired up (not clicked)
    page.goto(base + "/")
    assert page.query_selector('.stopall[hx-post="/kill"]') and page.query_selector('[hx-post="/logout"]')
    assert not errors, errors
    assert not server_errors, server_errors
    assert not problems, "\n".join(problems)
