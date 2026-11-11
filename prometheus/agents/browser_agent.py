"""Browser automation: open sites, read them, type, click, sign in, in Prometheus's own visible browser window."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.connectors.browser import BrowserError, kind_of_click

SESSION = None          # the app's BrowserSession


@register
class BrowserAgent(Agent):
    name = "browser"
    description = ("Uses websites in Prometheus's own browser window, step by step: open (url), read (what's on the "
                   "page, with its buttons and fields), type (field = its label or placeholder, text), click (label = "
                   "the button or link text), login (when the page asks for a password: the owner types it). "
                   "Read the page before typing or clicking. Paying or ordering waits on the Approvals page; "
                   "signing in, submitting, sending or posting is asked in the chat.")
    tools = {"web_fetch"}
    accepts_private = False
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "web_fetch")
        if SESSION is None:
            return StepResult("failed", "Browser control isn't set up.")
        action = str(inputs.get("action") or "").strip().lower()
        try:
            if action == "open":
                url = str(inputs.get("url") or inputs.get("target") or "").strip()
                if not url.startswith(("https://", "http://")):
                    url = "https://" + url
                from prometheus.safety.web import UnsafeURL, check_url

                try:
                    check_url(url)
                except (UnsafeURL, ValueError) as e:
                    return StepResult("failed", f"I won't open that: {e}")
                return StepResult("done", SESSION.open(url))
            if action == "read":
                page = SESSION.read()
                body = (f"{page['title']} ({page['url']})\n{page['text']}\nButtons and links: "
                        f"{', '.join(page['buttons'][:40])}\nFields: "
                        f"{', '.join(f['label'] + ' (' + f['type'] + ')' for f in page['fields'])}")
                return StepResult("done", untrusted(body, page["url"]), {"url": page["url"]})
            if action == "type":
                return StepResult("done", SESSION.type(str(inputs.get("field") or ""), str(inputs.get("text") or "")))
            if action == "click":
                label = str(inputs.get("label") or inputs.get("text") or "").strip()
                kind = kind_of_click(label)
                if kind == "pay":
                    ctx.approvals.request("browser_click", f"In the browser, press '{label}' (this can spend money)",
                                          {"label": label})
                    return StepResult("waiting_approval", f"Pressing '{label}' can spend money: approve it on the "
                                                          "Approvals page.")
                if kind == "send":
                    return ctx.ask("browser_click", f"In the browser, press '{label}'", {"label": label},
                                   exact=f"click:{label.lower()}", waiting=f"Press '{label}'? Answer below in the chat.")
                return StepResult("done", SESSION.click(label))
            if action == "login":
                ctx.approvals.request("browser_password", "The browser page needs your password",
                                      {"site": str(inputs.get("site") or "")}, secret_label="Password for this site")
                return StepResult("waiting_approval", "Type the password on the Approvals page; it goes straight into "
                                                      "the page and is never saved or shown to an AI.")
        except BrowserError as e:
            return StepResult("failed", str(e))
        return StepResult("failed", "I can: open, read, type, click, login.")


def click_handler(payload: dict) -> str:
    try:
        return SESSION.click(str(payload.get("label", "")))
    except BrowserError as e:
        raise RuntimeError(str(e)) from e


def password_handler(payload: dict, secret: str) -> str:
    try:
        return SESSION.fill_password(secret)
    except BrowserError as e:
        raise RuntimeError(str(e)) from e
