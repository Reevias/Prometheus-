"""M2: click every visible control of the new interface in a real browser. A control that does nothing (no change
on the page, no request, no navigation, no question) fails the test, and so does any page error or server error.

Needs Playwright with Chromium (pip install -e ".[browser]"; playwright install chromium). The GitHub "Click
through" job runs it on Windows; elsewhere it is skipped when the browser isn't available.
Buttons that destroy or stop things (Delete, Stop all, Log out, the window buttons) are checked for being wired up,
never clicked.
"""
from __future__ import annotations

import re
import socket
from pathlib import Path
import threading
import time

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PASSWORD = "correct horse battery"
LONG_CHAT = [0]
SKIP = ('[data-win]', '.stopall', '[data-act="delete"]', '[hx-post="/logout"]', '[hx-post*="/delete"]',
        '[data-api$="/delete"]', '#lightbox-close', '.danger')
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
    long_chat = st.create_chat("Long answer")                      # laptop test: long answers fold
    st.add_message(long_chat, "user", "explain everything")
    st.add_message(long_chat, "assistant", "\n\n".join(f"Point {i}: " + "words " * 30 for i in range(40)), "fake")
    LONG_CHAT[0] = long_chat
    from prometheus import projects as pj                         # a project to click through (M2.3)
    pid = pj.create(st, services.policy, tmp_home.home, "Demo project", about="short answers")
    pj.add_note(st, pid, "a note")
    pj.set_chat_project(st, chat, pid)
    (Path(pj.get(st, pid)["folder"]) / "notes.txt").write_text("hello")
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
    # Resolve the same control after each reload. Menus, chat messages and task rows can change order.
    return page.locator(sel).evaluate_all("""(elements, skip) => {
        const counts = new Map(), controls = [];
        for (const el of elements) {
            if (el.disabled || el.closest(skip)) continue;
            const label = (el.getAttribute('aria-label') || el.title || el.innerText || '').trim();
            let selector = el.localName;
            for (const key of ['id', 'aria-label', 'title', 'data-act', 'data-api', 'data-task', 'href', 'hx-post']) {
                const value = el.getAttribute(key);
                if (value) selector += '[' + key + '=' + CSS.escape(value) + ']';
            }
            const text = selector === el.localName ? el.textContent.trim() : null;
            const key = JSON.stringify([selector, text]);
            const occurrence = counts.get(key) || 0;
            counts.set(key, occurrence + 1);
            controls.push([selector, text, occurrence, label]);
        }
        return controls;
    }""", ','.join(SKIP))


def open_control_page(page, url):
    # The previous click may finish a delayed redirect as the next probe starts.
    try:
        page.goto(url)
    except sync_api.Error as exc:
        if "net::ERR_ABORTED" not in str(exc) and "interrupted by another navigation" not in str(exc):
            raise
        page.wait_for_load_state("domcontentloaded")
        page.goto(url)
    page.wait_for_load_state("networkidle")


def click_all(page, url, problems, seen):
    open_control_page(page, url)
    controls = visible_controls(page)
    last = None
    for selector, text, occurrence, label in controls:
        open_control_page(page, url)
        if "/login" in page.url:
            problems.append(f"{url}: signed out after clicking '{last}'")
            return
        last = label
        page.evaluate(PROBE)
        matches = page.locator(selector + ":visible")
        if text is not None:
            matches = matches.filter(has_text=re.compile("^" + re.escape(text) + "$"))
        control = matches.nth(occurrence)
        if not control.count():
            continue  # an earlier action may have removed the row

        seen["requests"] = seen["asked"] = 0
        before = page.url
        try:
            control.click(timeout=10000)
        except Exception as e:
            problems.append(f"{url}: couldn't click '{label}': {e}")
            continue
        page.wait_for_timeout(350)
        if "/login" in page.url:
            problems.append(f"{url}: '{label}' signed the owner out")
            return
        changed = seen["requests"] or seen["asked"] or page.url != before
        if not changed:
            # A click can navigate between reading the URL and inspecting the old document.
            try:
                changed = page.evaluate("window.__events !== 0")
            except sync_api.Error as exc:
                if "Execution context was destroyed" not in str(exc):
                    raise
                page.wait_for_load_state("domcontentloaded")
                changed = page.evaluate("window.__events !== 0")
        if not changed:
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
    for path in ("/", "/v3/chats", f"/c/{chat}", "/v3/agents", "/v3/agents?show=code", "/v3/tasks", "/v3/tasks/1",
                 "/v3/skills", "/v3/skills/research", "/v3/skills?tab=tools", "/v3/memory", "/v3/memory?tab=files",
                 "/v3/memory?tab=answers", "/v3/memory?tab=topics", "/v3/system", "/v3/system?tab=devices", "/v3/system?tab=activity", "/v3/approvals",
                 "/v3/automations", "/v3/research", "/v3/research?mode=study", "/v3/research?mode=money",
                 "/v3/connections", "/v3/tasks?view=board", "/v3/tasks?view=timeline", "/v3/tasks?view=table",
                 "/v3/automations/new", "/v3/projects", "/v3/projects/1", "/v3/projects/1?tab=chats",
                 "/v3/projects/1?tab=tasks", "/v3/projects/1?tab=agents", "/v3/projects/1?tab=notes",
                 "/v3/projects/1?tab=settings"):
        click_all(page, base + path, problems, seen)

    # Settings: the category menu leads to a real section every time (its forms are tested elsewhere)
    page.goto(base + "/settings")
    for link in page.query_selector_all(".set-nav a:visible"):
        target = link.get_attribute("href")
        assert target.startswith("#") and page.query_selector(target), target
    # every page in the sidebar and in More opens without an error
    page.goto(base + "/")
    # (the sidebar has no More group any more: every page is a link or in your menu)
    links = [a.get_attribute("href") for a in page.query_selector_all(".sidebar a[href]")]
    sections = {h.split("#", 1)[1] for h in links if "#" in h}       # your menu links to sections of Settings
    for href in dict.fromkeys(h.split("#", 1)[0] for h in links):    # each page once (a #section isn't a new load)
        r = page.goto(base + href)
        assert r is not None and r.status < 400, href
    page.goto(base + "/settings")
    for section in sections:
        assert page.query_selector("#" + section), section
    # The dangerous buttons are wired up without deleting the test task.
    page.goto(base + "/v3/tasks?view=table")
    assert page.locator('[aria-label="Delete task"][data-api$="/delete"][data-confirm]').count() > 0
    page.goto(base + "/")
    assert page.query_selector('.stopall[hx-post="/kill"]') and page.query_selector('[hx-post="/logout"]')
    assert not errors, errors
    assert not server_errors, server_errors
    assert not problems, "\n".join(problems)


def test_phone_width_fits(live, browser):
    """M2.4: at phone width, the command bar's buttons sit on one row under the text box, nothing runs off the
    side, the right panel is gone, and the menu button opens the sidebar as a drawer."""
    base, chat = live
    page = browser.new_page(viewport={"width": 360, "height": 740})
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    for path in ("/", f"/c/{chat}"):
        page.goto(base + path)
        # the test has no voice and one short AI name: add a mic and a long name, as on the laptop
        page.evaluate("() => { if (!document.querySelector('#mic')) document.querySelector('#send').insertAdjacentHTML"
                      "('beforebegin', '<button type=\"button\" class=\"mic\" id=\"mic\">m</button>'); }")
        page.evaluate("() => { var c = document.querySelector('#model-chip'); if (!c) { document.querySelector('#send')"
                      ".insertAdjacentHTML('beforebegin', '<select class=\"model-chip\" id=\"model-chip\"></select>');"
                      " c = document.querySelector('#model-chip'); } c.add(new Option('Gemini 2.5 Flash (Google, free)')); }")
        tops = page.evaluate("() => ['#plus', '#research-toggle', '#model-chip', '#mic', '#send'].map(s => document.querySelector(s))"
                             ".filter(Boolean).map(e => Math.round(e.getBoundingClientRect().top))")
        assert max(tops) - min(tops) < 12, (path, tops)        # one row (the chip is shorter, so it sits a little lower)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"), path
        assert not page.is_visible(".rpanel")
    for path in ("/v3/tasks", "/v3/system?tab=devices", "/v3/projects", "/v3/automations", "/v3/research",
                 "/v3/approvals", "/v3/agents", "/v3/skills", "/v3/research?mode=money", "/v3/memory", "/v3/system",
                 "/settings#general", "/settings#ai", "/settings#voice", "/settings#connections", "/settings#advanced"):
        page.goto(base + path)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth + 1"), path
    page.goto(base + "/")
    assert page.evaluate("() => document.querySelector('.sidebar').getBoundingClientRect().right <= 0")
    page.click("#menu-btn")
    page.wait_for_timeout(300)
    assert page.evaluate("() => document.querySelector('.sidebar').getBoundingClientRect().left >= 0")


def test_assistant_activity_on_phone_and_desktop(live, browser):
    from playwright.sync_api import expect

    base, _ = live
    page = browser.new_page(viewport={"width": 390, "height": 844})
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    label = page.locator("#assistant-state")
    expect(label).to_be_hidden()
    activity = {"state": "waiting_approval", "label": "Waiting for your approval"}

    def sample(route):
        response = route.fetch()
        state = response.json()
        state["assistant"] = activity
        route.fulfill(response=response, json=state)

    page.route("**/api/v3/state", sample)
    expect(label).to_have_text("Waiting for your approval")
    expect(label).to_be_visible()
    page.set_viewport_size({"width": 1400, "height": 900})
    activity.update(state="acting", label="Running round 2")
    expect(label).to_have_text("Running round 2")
    activity.update(state="idle", label="Idle")
    expect(label).to_be_hidden()


def test_chat_notes_add_and_delete(live, browser):
    """Review M2.4 #1: the Notes popover's buttons must reach the shared button handler (they did nothing)."""
    base, chat = live
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    page.goto(f"{base}/c/{chat}")
    page.once("dialog", lambda d: d.accept("buy milk"))
    page.click("#notes-btn")
    page.click("#notes-pop [data-api$='/notes']")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    page.click("#notes-btn")
    assert "buy milk" in page.inner_text("#notes-pop")
    page.once("dialog", lambda d: d.accept())
    page.click("#notes-pop .note-row button")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    assert "buy milk" not in page.content()


def test_hold_to_talk_from_home_reads_the_answer_out(live, browser):
    """Review M2.4 #6: a question spoken on Home opens a new chat; its answer must still be read out loud."""
    base, chat = live
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True)
    ctx.add_init_script("""
      window.__spoken = [];
      window.SpeechRecognition = function () { var r = this;
        this.start = function () { setTimeout(function () { r.onresult({ results: [[{ transcript: "hello there" }]] }); if (r.onend) r.onend(); }, 50); };
        this.stop = function () {}; };
      window.SpeechSynthesisUtterance = function (t) { this.text = t; };
      Object.defineProperty(window, "speechSynthesis", { value: { speak: function (u) {
        var a = JSON.parse(sessionStorage.getItem("spoken") || "[]"); a.push(u.text); sessionStorage.setItem("spoken", JSON.stringify(a)); } } });
    """)
    page = ctx.new_page()
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    page.dispatch_event("#talk", "pointerdown")
    page.wait_for_url(lambda u: "/c/" in u, timeout=10000)
    page.wait_for_function("() => JSON.parse(sessionStorage.getItem('spoken') || '[]').length > 0", timeout=15000)
    spoken = page.evaluate("() => JSON.parse(sessionStorage.getItem('spoken'))")
    assert len(spoken) == 1 and "answer" in spoken[0]
    ctx.close()


def test_long_answers_fold_and_open(live, browser):
    """Laptop test (20 Nov): long outputs couldn't be shrunk. They fold with Show more / Show less."""
    base, _ = live
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    page.goto(f"{base}/c/{LONG_CHAT[0]}")
    body = page.locator(".msg.assistant > .body").last
    page.wait_for_selector(".fold-btn")
    folded = body.bounding_box()["height"]
    assert folded <= 330 and page.inner_text(".fold-btn") == "Show more"
    page.click(".fold-btn")
    assert body.bounding_box()["height"] > 1000 and page.inner_text(".fold-btn") == "Show less"
    page.wait_for_timeout(5500)                      # the chat refreshes itself: it stays open
    assert page.locator(".msg.assistant > .body").last.bounding_box()["height"] > 1000


def test_the_name_box_saves_your_name(live, browser):
    """Laptop check (20 Nov): the name was forgotten; the box is now the page's own (no browser pop-up)."""
    base, _ = live
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    page.click(".profile-name")
    page.fill("#name-dialog input", "Saiveer")
    page.click("#name-dialog button[type=submit]")
    page.wait_for_load_state("networkidle")
    page.wait_for_timeout(500)
    assert "Saiveer" in page.inner_text(".profile-name")
    page.goto(base + "/v3/chats")
    assert "Saiveer" in page.inner_text(".profile-name")


FAKE_WINDOW = """
window.__calls = [];
window.pywebview = {api: {
  window_action: function (a) { window.__calls.push(["action", a]); return Promise.resolve(true); },
  resize_to: function (w, h, e) { window.__calls.push(["resize", w, h, e]); return Promise.resolve(true); }
}};
"""


def test_window_buttons_and_edges_talk_to_the_window_itself(live, browser):
    """Laptop check (20 Nov): minimise, maximise and fullscreen did nothing when the server was the background copy.
    The page now calls the window directly (pywebview js_api); its edges resize it."""
    base, _ = live
    page = browser.new_page(viewport={"width": 1400, "height": 900})
    page.add_init_script(FAKE_WINDOW)
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    page.click("[data-win=minimize]")
    page.click("[data-win=maximize]")
    page.keyboard.press("F11")
    assert page.evaluate("window.__calls") == [["action", "minimize"], ["action", "maximize"], ["action", "fullscreen"]]
    page.keyboard.press("F11")                                         # back out of fullscreen
    page.evaluate("window.__calls = []")
    box = page.locator(".grip-e").bounding_box()
    x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x - 200, y, steps=4)
    page.mouse.up()
    page.wait_for_timeout(300)
    calls = page.evaluate("window.__calls")
    assert calls and all(c[0] == "resize" and c[3] == "e" for c in calls)
    assert calls[-1][1] < page.evaluate("window.outerWidth")             # dragged inwards: narrower


def _signed_in(live, browser, width=1400, height=900):
    base, chat = live
    page = browser.new_page(viewport={"width": width, "height": height})
    page.goto(base + "/login")
    page.fill("input[name=password]", PASSWORD)
    page.press("input[name=password]", "Enter")
    page.wait_for_url(base + "/")
    page.wait_for_selector("#side-lists .side-head", state="attached")    # loaded (hidden in the icon sidebar)
    return page, base, chat


def test_one_click_opens_a_recent_chat(live, browser):
    page, base, _ = _signed_in(live, browser)
    chat = LONG_CHAT[0]                                  # not in a project: listed under Recents
    page.click(f'#side-lists a.side-chat[href="/c/{chat}"]')
    page.wait_for_url(f"{base}/c/{chat}")
    page.wait_for_selector(f'#side-lists a.side-chat.on[href="/c/{chat}"]')


def test_a_project_chat_is_two_clicks_and_the_fold_is_remembered(live, browser):
    page, base, chat = _signed_in(live, browser)
    project = page.locator("#side-lists details.side-project").first
    project.locator("summary .side-pname").click()
    assert project.get_attribute("open") is not None
    page.reload()
    page.wait_for_selector("#side-lists details.side-project[open]")
    link = page.locator("#side-lists details.side-project[open] a.side-chat").first
    href = link.get_attribute("href")
    link.click()
    page.wait_for_url(base + href)


def test_ctrl_k_still_finds_every_other_page(live, browser):
    """Fewer pages (20 Nov): the sidebar shows four; Memory, System, Skills... are found by Search (Ctrl+K)."""
    page, base, _ = _signed_in(live, browser)
    page.click("#side-search")
    page.fill("#palette-input", "Memory")
    page.wait_for_timeout(200)
    assert "Memory" in page.inner_text("#palette-list")
    page.keyboard.press("Enter")
    page.wait_for_url(base + "/v3/memory")

def test_rename_and_delete_from_the_chat_menu(live, browser):
    page, base, chat = _signed_in(live, browser)
    page.goto(f"{base}/c/{chat}")
    page.wait_for_selector(f'#side-lists [data-chat-menu="{chat}"]')
    page.hover(f'#side-lists a.side-chat[href="/c/{chat}"]')
    page.click(f'#side-lists [data-chat-menu="{chat}"]')
    page.click("#chat-menu [data-act=rename]")
    page.fill("#rename-dialog input", "Renamed here")
    page.click("#rename-dialog button[type=submit]")
    page.wait_for_selector("#side-lists a.side-chat:has-text('Renamed here')")
    page.click(f'#side-lists [data-chat-menu="{chat}"]')
    page.once("dialog", lambda d: d.accept())
    page.click("#chat-menu [data-act=delete]")
    page.wait_for_url(base + "/v3/chats")
    page.wait_for_selector("#side-lists .side-head")
    assert page.locator(f'#side-lists a[href="/c/{chat}"]').count() == 0


def test_shrunk_sidebar_shows_icons_only(live, browser):
    page, _, _ = _signed_in(live, browser)
    page.keyboard.press("Control+b")
    page.wait_for_timeout(300)
    assert not page.locator("#side-lists").is_visible()
    assert page.locator("#side-new-chat").is_visible() and page.locator("#side-search").is_visible()



def test_the_four_pages_fit_in_the_sidebar_in_both_widths(live, browser):
    """The sidebar row (Home, Tasks, Abilities, Study & Money) stays inside the sidebar, also shrunk."""
    page, _, _ = _signed_in(live, browser)
    for shrink in (False, True):
        if shrink:
            page.keyboard.press("Control+b")
            page.wait_for_timeout(300)
        side = page.locator("#sidebar").bounding_box()
        for link in page.locator(".nav-row > a").all():
            b = link.bounding_box()
            assert b["x"] >= side["x"] and b["x"] + b["width"] <= side["x"] + side["width"] + 1
        assert page.evaluate("document.scrollingElement.scrollLeft") == 0

def test_search_works_from_the_phone_drawer(live, browser):
    """Review: on a phone, Search opened the palette under the drawer, so nothing seemed to happen."""
    page, _, _ = _signed_in(live, browser, width=390, height=800)
    page.click("#menu-btn")
    page.wait_for_timeout(300)
    page.click("#side-search")
    page.wait_for_timeout(300)
    assert page.locator("#palette-input").is_visible()
    assert "drawer-open" not in (page.get_attribute("#app", "class") or "")
    page.keyboard.type("Example")
    assert page.locator("#palette-list").is_visible()


def test_a_narrow_window_shows_a_tidy_icon_sidebar(live, browser):
    """Review: windows 701-860 px wide showed cut-off lists ("PROJ", "RECE") and a spilling Search label."""
    page, _, _ = _signed_in(live, browser, width=800, height=800)
    assert not page.locator("#side-lists").is_visible()
    assert not page.locator("#side-search span").is_visible() and not page.locator("#side-search kbd").is_visible()
    side = page.locator("#sidebar").bounding_box()
    for sel in ("#side-new-chat", "#side-search", ".nav-row > a >> nth=3"):
        b = page.locator(sel).bounding_box()
        assert b["x"] + b["width"] <= side["x"] + side["width"] + 1, sel


def _visible_sections(page):
    return [s.get_attribute("id") for s in page.locator("section.set:visible").all()]


def test_settings_show_one_tab_at_a_time(live, browser):
    """Owner (20 Nov): "voice only shows everything connected to voice, not if you scroll down you can read others"."""
    page, base, _ = _signed_in(live, browser)
    page.goto(base + "/settings#voice")
    page.wait_for_timeout(300)
    assert _visible_sections(page) == ["voice"]
    assert page.locator('.page-tabs a.on').inner_text().strip() == "Voice"
    assert page.locator(".set-body .page-tabs").bounding_box()["y"] >= 0     # the tab bar isn't scrolled away
    page.click('.page-tabs a[data-tab="ai"]')
    page.wait_for_timeout(200)
    assert set(_visible_sections(page)) == {"ai", "costs", "practice-tests"}
    page.goto(base + "/settings#money-tracker")                 # an old link to a section opens its tab
    page.wait_for_timeout(300)
    assert "money-tracker" in _visible_sections(page) and page.locator('.page-tabs a.on').inner_text().strip() == "Advanced"
    page.fill("#set-search", "wake")                            # search looks in every tab
    page.wait_for_timeout(200)
    assert "voice" in _visible_sections(page)
    page.fill("#set-search", "")
    page.goto(base + "/settings#voice")
    page.wait_for_timeout(300)
    page.locator('#voice form [type=submit], #voice button[hx-post]').first.click()   # a save re-renders the page
    page.wait_for_timeout(800)
    assert _visible_sections(page) == ["voice"]


def test_research_button_sends_one_deep_search_then_turns_off(live, browser):
    """Owner (20 Nov): Research merged into chat as a button in the chat box, like a mode."""
    page, base, chat = _signed_in(live, browser)
    page.goto(f"{base}/c/{chat}")
    sent = []
    page.on("request", lambda r: sent.append(r.post_data or "") if r.url.endswith("/api/v3/send") else None)
    page.click("#research-toggle")
    assert page.get_attribute("#research-toggle", "aria-pressed") == "true"
    page.fill("#command-input", "solar panels in India")
    page.press("#command-input", "Enter")
    page.wait_for_timeout(800)
    assert sent and "@research solar panels in India" in sent[0]
    assert page.get_attribute("#research-toggle", "aria-pressed") == "false"


def test_the_chat_uses_the_width_when_the_panel_is_closed(live, browser):
    """Owner (20 Nov): with everything closed the chat stayed a narrow column."""
    page, base, chat = _signed_in(live, browser, width=1800, height=900)
    page.goto(f"{base}/c/{chat}")
    page.wait_for_selector("#messages")
    page.evaluate("document.getElementById('app').classList.add('no-panel', 'mini')")
    page.wait_for_timeout(200)
    assert page.locator("#messages").bounding_box()["width"] >= 1150
    assert page.locator("#command").bounding_box()["width"] >= 1100
