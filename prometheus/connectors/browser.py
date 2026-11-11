"""Browser automation in Prometheus's own visible browser window (Microsoft Edge with a separate profile, so it
never sees the owner's normal saved passwords). Uses Playwright; every command runs on one worker thread because
Playwright objects must stay on the thread that made them.
"""
from __future__ import annotations

import queue
import re
import threading
from pathlib import Path

PAGE_CHARS = 8000
RISKY = re.compile(r"\b(pay|buy|purchase|order|checkout|check out|place order|subscribe|donate|confirm payment|"
                   r"add card|transfer)\b", re.I)
SENDS = re.compile(r"\b(submit|send|post|publish|sign in|log in|login|sign up|register|delete|remove|confirm|"
                   r"save|apply|book|reply|share|tweet|comment)\b", re.I)


class BrowserError(Exception):
    pass


def kind_of_click(label: str, info: dict | None = None) -> str:
    """'pay' (Approvals page), 'send' (asked in the chat) or 'plain' (just do it). With `info` (what the element
    really is), its real name, ids and classes count too, and anything that submits a form is at least 'send'."""
    words = " ".join([label or ""] + ([str(info.get("name", "")), str(info.get("extra", "")),
                                       str(info.get("href", ""))] if info else []))
    if RISKY.search(words) or re.search(r"(pay|buy|order|checkout|purchase|cart)", words.replace("-", " "), re.I):
        return "pay"
    if SENDS.search(words):
        return "send"
    if info and (info.get("type") == "submit" or (info.get("tag") == "button" and info.get("form")
                                                  and info.get("type") != "button")):
        return "send"
    return "plain"


class BrowserSession:
    def __init__(self, profile: Path, headless: bool = False, channel: str | None = "msedge", executable: str = ""):
        self.profile, self.headless, self.channel, self.executable = Path(profile), headless, channel, executable
        self.paused = False             # the owner pressed "Take over"
        self._q: queue.Queue = queue.Queue()
        self._thread: threading.Thread | None = None
        self._page = None
        self._ctx = None

    # -- the worker thread ---------------------------------------------------------------
    def _worker(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            err = BrowserError('Browser control needs one extra package. Run:  pip install -e ".[browser]"')
            while True:
                _, reply = self._q.get()
                reply.put(err)
        with sync_playwright() as pw:
            while True:
                fn, reply = self._q.get()
                if fn is None:
                    break
                try:
                    if self._ctx is None:
                        self.profile.mkdir(parents=True, exist_ok=True)
                        opts = {"headless": self.headless, "viewport": {"width": 1280, "height": 800}}
                        if self.executable:
                            opts["executable_path"] = self.executable
                        elif self.channel:
                            opts["channel"] = self.channel
                        self._ctx = pw.chromium.launch_persistent_context(str(self.profile), **opts)
                        self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
                    reply.put(fn(self._page))
                except Exception as e:     # a page that misbehaves never kills the worker
                    first = str(e).splitlines()[0] if str(e) else type(e).__name__
                    if self._ctx is None and ("is not found" in first or "Executable doesn't exist" in first):
                        reply.put(BrowserError("I couldn't open my browser window: Microsoft Edge isn't installed (or "
                                               "Playwright isn't set up). Run the installer again to fix it."))
                    else:
                        reply.put(BrowserError(f"The web page had a problem: {first[:200]}"))
            if self._ctx is not None:
                self._ctx.close()

    def run(self, fn, timeout: float = 60):
        if self.paused:
            raise BrowserError("You've taken over the browser. Press 'Hand back' when you're done.")
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._worker, name="prometheus-browser", daemon=True)
            self._thread.start()
        reply: queue.Queue = queue.Queue()
        self._q.put((fn, reply))
        try:
            out = reply.get(timeout=timeout)
        except queue.Empty as e:
            raise BrowserError("The browser didn't answer in time.") from e
        if isinstance(out, Exception):
            raise out
        return out

    def close(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._q.put((None, None))

    # -- what agents can do -------------------------------------------------------------------
    def open(self, url: str) -> str:
        def go(page):
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return f"Opened {page.title() or url}"
        return self.run(go)

    def read(self) -> dict:
        def look(page):
            text = page.inner_text("body", timeout=10000)
            controls = page.eval_on_selector_all(
                "a, button, input[type=submit], input[type=button], [role=button]",
                "els => els.filter(e => e.offsetParent !== null).slice(0, 60).map(e => (e.innerText || e.value || "
                "e.getAttribute('aria-label') || '').trim()).filter(t => t)")
            fields = page.eval_on_selector_all(
                "input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, select",
                "els => els.filter(e => e.offsetParent !== null).slice(0, 30).map(e => ({label: (e.labels && "
                "e.labels[0] ? e.labels[0].innerText : '') || e.placeholder || e.name || e.id || e.type, "
                "type: e.type || e.tagName.toLowerCase()}))")
            return {"url": page.url, "title": page.title(), "text": " ".join(text.split())[:PAGE_CHARS],
                    "buttons": list(dict.fromkeys(c[:60] for c in controls)), "fields": fields}
        return self.run(look)

    def _field(self, page, label: str):
        for finder in (lambda: page.get_by_label(label, exact=False), lambda: page.get_by_placeholder(label, exact=False),
                       lambda: page.locator(f"[name='{label}'], #{re.sub(r'[^A-Za-z0-9_-]', '', label) or 'x'}")):
            loc = finder()
            if loc.count():
                return loc.first
        raise BrowserError(f"There's no field called '{label}' on this page.")

    def type(self, label: str, text: str) -> str:
        def fill(page):
            field = self._field(page, label)
            if (field.get_attribute("type") or "").lower() == "password":
                raise BrowserError("Passwords are never typed by Prometheus. Use 'login' and type it yourself.")
            field.fill(text)
            return f"Typed into '{label}'."
        return self.run(fill)

    @staticmethod
    def _locate(page, label: str, exact: bool):
        for role in ("button", "link"):
            loc = page.get_by_role(role, name=label, exact=exact)
            if loc.count():
                return loc.first, role
        loc = page.get_by_text(label, exact=exact)
        return (loc.first, "text") if loc.count() else (None, "")

    def inspect(self, label: str) -> dict:
        """What pressing `label` would really press: its full name, kind and whether it submits a form.
        The click rules are decided on this, never on the words the AI typed ("Place ord" is "Place order")."""
        def look(page):
            loc, role = self._locate(page, label, exact=True)
            if loc is None:
                loc, role = self._locate(page, label, exact=False)
            if loc is None:
                raise BrowserError(f"There's nothing called '{label}' to click on this page.")
            info = loc.evaluate("e => ({name: (e.innerText || e.value || e.getAttribute('aria-label') || '').trim(), "
                                "tag: e.tagName.toLowerCase(), type: (e.getAttribute('type') || '').toLowerCase(), "
                                "form: !!e.closest('form'), href: e.getAttribute('href') || '', "
                                "extra: [e.id, e.className, e.getAttribute('name'), e.getAttribute('data-testid'), "
                                "e.getAttribute('title')].filter(Boolean).join(' ')})")
            info["role"] = role
            return info
        return self.run(look)

    def click(self, label: str, exact: bool = False) -> str:
        def press(page):
            loc, _ = self._locate(page, label, exact=True)
            if loc is None and not exact:
                loc, _ = self._locate(page, label, exact=False)
            if loc is None:
                raise BrowserError(f"There's nothing called '{label}' to click on this page.")
            loc.click(timeout=10000)
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            return f"Clicked '{label}'. Now on: {page.title()}"
        return self.run(press)

    def fill_password(self, secret: str) -> str:
        """Called only from the Approvals page, with what the owner typed. The password is never stored or shown."""
        def fill(page):
            field = page.locator("input[type=password]")
            if not field.count():
                raise BrowserError("This page has no password field.")
            field.first.fill(secret)
            field.first.press("Enter")
            page.wait_for_load_state("domcontentloaded", timeout=15000)
            return f"Signed in. Now on: {page.title()}"
        return self.run(fill)
