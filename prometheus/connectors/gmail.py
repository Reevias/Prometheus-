"""Gmail through the owner's Google sign-in (no app password): read, search and send, with the same results as the
IMAP mailbox so every email feature works the same. Reading never marks mail as read; sending still waits for the
owner's approval (the email agent decides that, not this file).
"""
from __future__ import annotations

import base64
import email
import email.utils
import re
from email.message import EmailMessage

from prometheus.connectors.email_box import ADDRESS, BODY_CHARS, SNIPPET, EmailError, _attachments, _header, _text_of
from prometheus.connectors.google import Google, GoogleError

API = "https://gmail.googleapis.com/gmail/v1/users/me"
MSG_ID = re.compile(r"^[0-9a-fA-F]{6,32}$")


def _query(unread: bool, sender: str, subject: str, text: str, days: int) -> str:
    def q(v: str) -> str:
        return '"' + re.sub(r'["\\\r\n]', " ", v).strip()[:100] + '"'

    parts = ["in:inbox"] if not (sender or subject or text) else []
    if unread:
        parts.append("is:unread")
    if sender:
        parts.append(f"from:{q(sender)}")
    if subject:
        parts.append(f"subject:{q(subject)}")
    if text:
        parts.append(q(text))
    if days:
        parts.append(f"newer_than:{int(days)}d")
    return " ".join(parts)


class GmailBox:
    def __init__(self, google: Google | None = None):
        self.g = google or Google()
        self._address = ""

    def _get(self, path: str, **params):
        try:
            return self.g._call("GET", f"{API}/{path}", params=params).json()
        except GoogleError as e:
            raise EmailError(_explain(e)) from e

    @property
    def address(self) -> str:
        if not self._address:
            self._address = self._get("profile").get("emailAddress", "")
        return self._address

    def _message(self, mid: str) -> dict | None:
        data = self._get(f"messages/{mid}", format="raw")
        raw = base64.urlsafe_b64decode(data.get("raw", "") + "===")
        if not raw:
            return None
        msg = email.message_from_bytes(raw)
        body = " ".join(_text_of(msg).split())
        when = email.utils.parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
        return {"id": mid, "from": _header(msg["From"]), "subject": _header(msg["Subject"]) or "(no subject)",
                "date": when.strftime("%Y-%m-%d %H:%M") if when else "", "snippet": body[:SNIPPET],
                "body": body[:BODY_CHARS], "attachments": _attachments(msg)}

    def search(self, unread: bool = False, sender: str = "", subject: str = "", text: str = "", days: int = 0,
               limit: int = 10) -> list[dict]:
        found = self._get("messages", q=_query(unread, sender, subject, text, days), maxResults=max(1, min(limit, 50)))
        out = []
        for m in found.get("messages", [])[:limit]:
            got = self._message(m["id"])
            if got:
                out.append(got)
        return out

    def read(self, message_id: str) -> dict | None:
        if not MSG_ID.fullmatch(str(message_id)):
            raise EmailError("That isn't an email id.")
        return self._message(str(message_id))

    def send(self, to: str, subject: str, body: str) -> str:
        recipients = [a.strip() for a in re.split(r"[,;]", to or "") if a.strip()]
        if not recipients or not all(ADDRESS.fullmatch(a) for a in recipients) or len(recipients) > 20:
            raise EmailError("Give one or more valid email addresses (up to 20).")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.address, ", ".join(recipients), (subject or "")[:300]
        msg.set_content((body or "")[:50_000])
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        try:
            self.g._call("POST", f"{API}/messages/send", json={"raw": raw})
        except GoogleError as e:
            raise EmailError(_explain(e)) from e
        return f"Sent to {', '.join(recipients)}: {subject}"


def _explain(e: GoogleError) -> str:
    if "403" in str(e):
        return ("Gmail isn't allowed yet: in Settings press 'Sign in with Google' again and tick Gmail (and make "
                "sure the Gmail API is turned on in your Google Cloud project).")
    return str(e)
