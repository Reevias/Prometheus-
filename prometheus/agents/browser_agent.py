"""Browser automation: open sites, read them, type, click, sign in, in Prometheus's own visible browser window."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.connectors.browser import BrowserError, kind_of_click

SESSION = None          # the app's BrowserSession


@register
class BrowserAgent(Agent):
    name = "browser"
    description = ("Uses websites in Prometheus's own browser window on the laptop, step by step: open (url), read (what's "
                   "on the page, with its buttons and fields), snapshot (the page's accessibility tree: every element's "
                   "role, name and state with a short id like e12), type (field = its label or placeholder, text), click "
                   "(label = the button or link text), type, click or select (ref = an id from the last snapshot; "
                   "select also takes text = the option), login (when the page asks for a password: the owner types it). "
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
            if action == "snapshot":
                snap = SESSION.snapshot()
                body = f"{snap['title']} ({snap['url']})\n{snap['tree']}"
                return StepResult("done", untrusted(body, snap["url"]),
                                  {"url": snap["url"], "truncated": snap["truncated"]})
            if action in ("type", "click", "select") and "ref" in inputs:
                return self._by_ref(action, str(inputs.get("ref") or ""), str(inputs.get("text") or ""), ctx)
            if action == "type":
                return StepResult("done", SESSION.type(str(inputs.get("field") or ""), str(inputs.get("text") or "")))
            if action == "click":
                label = str(inputs.get("label") or inputs.get("text") or "").strip()
                info = SESSION.inspect(label)              # the real element, not the AI's words
                label = info.get("name") or label
                kind = kind_of_click(label, info)
                if kind == "pay":
                    ctx.approvals.request("browser_click", f"In the browser, press '{label}' (this can spend money)",
                                          {"label": label})
                    return StepResult("waiting_approval", f"Pressing '{label}' can spend money: approve it on the "
                                                          "Approvals page.")
                if kind == "send":
                    return ctx.ask("browser_click", f"In the browser, press '{label}'", {"label": label},
                                   exact=f"click:{label.lower()}", waiting=f"Press '{label}'? Answer below in the chat.")
                return StepResult("done", SESSION.click(label, exact=True))
            if action == "login":
                host = SESSION.current_host()                 # the page it would really go to, not what the plan says
                ctx.approvals.request("browser_password", f"The browser page at {host or 'this page'} needs your password",
                                      {"site": str(inputs.get("site") or ""), "host": host},
                                      secret_label=f"Password for {host or 'this site'}")
                return StepResult("waiting_approval", "Type the password on the Approvals page; it goes straight into "
                                                      "the page and is never saved or shown to an AI.")
        except BrowserError as e:
            return StepResult("failed", str(e))
        return StepResult("failed", "I can: open, read, type, click, login.")


    @staticmethod
    def _by_ref(action: str, ref: str, text: str, ctx: AgentContext) -> StepResult:
        """Acts on an element by its snapshot id. The click rules are decided on what the element really is."""
        if action == "type":
            return StepResult("done", SESSION.type_ref(ref, text))
        if action == "select":
            return StepResult("done", SESSION.select_ref(ref, text))
        info = SESSION.inspect_ref(ref)
        label = info.get("name") or ref
        kind = kind_of_click(label, info)
        payload = {"label": label, "ref": ref}
        # the name comes from the page, not the plan: the owner sees it (shortened), the model only gets it wrapped
        shown = label if len(label) <= 120 else label[:117] + "..."
        wrapped = untrusted(shown, "button name on the page")
        if kind == "pay":
            ctx.approvals.request("browser_click", f"In the browser, press '{shown}' (this can spend money)", payload)
            return StepResult("waiting_approval", f"Pressing element {ref} can spend money: approve it on the "
                                                  f"Approvals page. Its name: {wrapped}")
        if kind == "send":
            return ctx.ask("browser_click", f"In the browser, press '{shown}'", payload,
                           exact=f"click:{label.lower()}",
                           waiting=f"Pressing element {ref} needs a yes, asked below in the chat. Its name: {wrapped}")
        return StepResult("done", SESSION.click_ref(ref, expect_name=info.get("name", "")))


def click_handler(payload: dict) -> str:
    try:
        if payload.get("ref"):          # an id from a snapshot: only if it is still the same element
            return SESSION.click_ref(str(payload["ref"]), expect_name=str(payload.get("label", "")))
        return SESSION.click(str(payload.get("label", "")), exact=True)
    except BrowserError as e:
        raise RuntimeError(str(e)) from e


def password_handler(payload: dict, secret: str) -> str:
    try:
        return SESSION.fill_password(secret, expect_host=payload.get("host"))
    except BrowserError as e:
        raise RuntimeError(str(e)) from e
