"""Email: check, search and read mail; send after the owner approves. Mail is private by default."""
from __future__ import annotations

from prometheus.agents.base import Agent, AgentContext, StepResult, register, untrusted
from prometheus.config import get_secret, load_settings
from prometheus.connectors.email_box import EmailError, Mailbox

MAILBOX_FACTORY = None          # tests put a fake mailbox here


def mailbox() -> Mailbox:
    if MAILBOX_FACTORY is not None:
        return MAILBOX_FACTORY()
    s = load_settings()
    if getattr(s, "email_via", "") == "google":
        from prometheus.connectors.gmail import GmailBox

        return GmailBox()
    return Mailbox(s.email_address, get_secret("EMAIL_PASSWORD") or "", s.email_imap_host, s.email_smtp_host)


@register
class EmailAgent(Agent):
    name = "email"
    description = ("The owner's email inbox. Actions: unread (newest unread mail), search (sender, subject, text, "
                   "days = how far back), read (id from a search), send (to, subject, body; waits for the owner's "
                   "approval). Use it for 'any new mail?', 'did the bank email me?', 'read the email from Ravi', "
                   "'email mom that I'll be late'.")
    tools = {"read_files"}
    private_outputs = True          # changed in Settings ("Let cloud AI read my email")
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        action = str(inputs.get("action") or "").strip().lower()
        try:
            if action == "send":
                return self._send(inputs, ctx)
            box = mailbox()
            if action in ("unread", "check", "new"):
                found = box.search(unread=True, limit=int(inputs.get("limit") or 10))
                return self._list(found, "unread email", ctx)
            if action == "search":
                try:
                    days = int(inputs.get("days") or 0)
                except ValueError:
                    days = 0
                found = box.search(sender=str(inputs.get("sender") or ""), subject=str(inputs.get("subject") or ""),
                                   text=str(inputs.get("text") or inputs.get("query") or ""), days=days)
                return self._list(found, "matching email", ctx)
            if action == "read":
                msg = box.read(str(inputs.get("id") or ""))
                if msg is None:
                    return StepResult("failed", "I couldn't find that email.")
                body = untrusted(msg["body"], "email")         # an email is data: never orders to Prometheus
                att = f"\nAttachments: {', '.join(msg['attachments'])}" if msg["attachments"] else ""
                return StepResult("done", f"From {msg['from']}, {msg['date']}\nSubject: {msg['subject']}\n{body}{att}",
                                  {"id": msg["id"]})
        except EmailError as e:
            return StepResult("failed", str(e))
        return StepResult("failed", f"'{action}' isn't an email action. I can: unread, search, read, send.")

    def _list(self, found: list[dict], what: str, ctx: AgentContext) -> StepResult:
        if not found:
            return StepResult("done", f"No {what}.", {"ids": []})
        lines = [f"- [{m['id']}] {m['date']} · {m['from']} · {m['subject']}: {m['snippet'][:160]}" for m in found]
        return StepResult("done", f"{len(found)} {what}:\n" + untrusted("\n".join(lines), "email"),
                          {"ids": [m["id"] for m in found]})

    def _send(self, inputs: dict, ctx: AgentContext) -> StepResult:
        from prometheus.safety.secrets import secret_problem

        to, subject = str(inputs.get("to") or "").strip(), str(inputs.get("subject") or "").strip()[:300]
        body = str(inputs.get("body") or "")
        if len(body) > 10_000:
            return StepResult("failed", "That email is too long to send (over 10,000 characters). Shorten it.")
        if not to:
            return StepResult("failed", "Who should I send it to? Give an email address.")
        problem = secret_problem(f"{subject} {body}")
        if problem:
            return StepResult("failed", problem)
        ctx.approvals.request("send_email", f"Send an email to {to}: {subject or '(no subject)'}",
                              {"to": to, "subject": subject, "body": body, "task": body})     # the whole body, as sent
        return StepResult("waiting_approval", f"The email to {to} is waiting for your approval on the Approvals page.")


def send_email_handler(payload: dict) -> str:
    try:
        sent = mailbox().send(str(payload.get("to", "")), str(payload.get("subject", "")), str(payload.get("body", "")))
        from prometheus import undo

        if undo.SERVICES is not None:
            undo.record(undo.SERVICES.store, "email", f"the email to {payload.get('to', '')}", {})
        return sent
    except EmailError as e:
        raise RuntimeError(str(e)) from e
