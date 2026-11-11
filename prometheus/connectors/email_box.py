"""Email through the standard mail protocols (IMAP to read, SMTP to send). Works with Gmail, Outlook, Yahoo, iCloud
and any other provider, using an app password (no Google or Microsoft app registration needed).

Reading never marks mail as read (BODY.PEEK). Sending always goes through the Approvals page.
"""
from __future__ import annotations

import email
import email.utils
import html as htmllib
import imaplib
import re
import smtplib
import ssl
import time
from email.header import decode_header, make_header
from email.message import EmailMessage

PROVIDERS = {   # domain → (IMAP host, SMTP host)
    "gmail.com": ("imap.gmail.com", "smtp.gmail.com"), "googlemail.com": ("imap.gmail.com", "smtp.gmail.com"),
    "outlook.com": ("outlook.office365.com", "smtp.office365.com"),
    "hotmail.com": ("outlook.office365.com", "smtp.office365.com"),
    "live.com": ("outlook.office365.com", "smtp.office365.com"), "msn.com": ("outlook.office365.com", "smtp.office365.com"),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"), "ymail.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
    "icloud.com": ("imap.mail.me.com", "smtp.mail.me.com"), "me.com": ("imap.mail.me.com", "smtp.mail.me.com"),
}
APP_PASSWORD_HELP = {
    "gmail.com": "myaccount.google.com/apppasswords (needs 2-Step Verification on)",
    "outlook.com": "account.microsoft.com/security → Advanced security options → App passwords",
    "yahoo.com": "login.yahoo.com/account/security → Generate app password",
    "icloud.com": "account.apple.com → Sign-In and Security → App-Specific Passwords",
}
ADDRESS = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
SNIPPET = 300
BODY_CHARS = 8000
FETCH_BYTES = 120_000


class EmailError(Exception):
    pass


def hosts_for(address: str, imap_host: str = "", smtp_host: str = "") -> tuple[str, str]:
    domain = address.rsplit("@", 1)[-1].lower()
    guess = PROVIDERS.get(domain, (f"imap.{domain}", f"smtp.{domain}"))
    return imap_host or guess[0], smtp_host or guess[1]


def help_for(address: str) -> str:
    domain = (address or "").rsplit("@", 1)[-1].lower()
    for key, text in APP_PASSWORD_HELP.items():
        if domain == key or (key == "outlook.com" and domain in ("hotmail.com", "live.com", "msn.com")):
            return text
    return "your email provider's security settings (look for 'app password')"


def _header(value) -> str:
    try:
        return str(make_header(decode_header(value or ""))).strip()
    except Exception:
        return str(value or "").strip()


def _text_of(msg: email.message.Message) -> str:
    """The readable text of a message: the plain part, or the HTML part turned into text."""
    plain, html_part = "", ""
    for part in msg.walk() if msg.is_multipart() else [msg]:
        if part.get_content_maintype() == "multipart" or part.get("Content-Disposition", "").startswith("attachment"):
            continue
        try:
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        except Exception:
            continue
        if part.get_content_type() == "text/plain" and not plain:
            plain = text
        elif part.get_content_type() == "text/html" and not html_part:
            html_part = text
    if plain:
        return plain
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html_part)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>", "\n", text)
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", text))


def _attachments(msg: email.message.Message) -> list[str]:
    return [_header(p.get_filename()) for p in msg.walk() if p.get_filename()]


class Mailbox:
    def __init__(self, address: str, password: str, imap_host: str = "", smtp_host: str = "",
                 imap_factory=None, smtp_factory=None):
        if not ADDRESS.fullmatch(address or ""):
            raise EmailError("Set your email address in Settings first.")
        if not password:
            raise EmailError("Add your email app password in Settings first.")
        self.address, self.password = address, password
        self.imap_host, self.smtp_host = hosts_for(address, imap_host, smtp_host)
        self._imap_factory = imap_factory or (lambda host: imaplib.IMAP4_SSL(host, 993, ssl_context=ssl.create_default_context(), timeout=30))
        self._smtp_factory = smtp_factory or (lambda host: smtplib.SMTP(host, 587, timeout=30))

    # -- reading ----------------------------------------------------------------------
    def _open(self):
        try:
            box = self._imap_factory(self.imap_host)
            box.login(self.address, self.password)
            box.select("INBOX", readonly=True)       # read-only: nothing gets marked as read or moved
            return box
        except imaplib.IMAP4.error as e:
            raise EmailError("The email provider refused the login. Check the app password in Settings.") from e
        except OSError as e:
            raise EmailError(f"Couldn't reach {self.imap_host} ({type(e).__name__}).") from e

    def _fetch(self, box, uids: list[bytes]) -> list[dict]:
        out = []
        for uid in uids:
            status, data = box.uid("fetch", uid, f"(BODY.PEEK[]<0.{FETCH_BYTES}>)")
            raw = next((part[1] for part in data or [] if isinstance(part, tuple)), b"")
            if status != "OK" or not raw:
                continue
            msg = email.message_from_bytes(raw)
            body = " ".join(_text_of(msg).split())
            when = email.utils.parsedate_to_datetime(msg["Date"]) if msg["Date"] else None
            out.append({"id": uid.decode() if isinstance(uid, bytes) else str(uid), "from": _header(msg["From"]),
                        "subject": _header(msg["Subject"]) or "(no subject)",
                        "date": when.strftime("%Y-%m-%d %H:%M") if when else "", "snippet": body[:SNIPPET],
                        "body": body[:BODY_CHARS], "attachments": _attachments(msg)})
        return out

    def search(self, unread: bool = False, sender: str = "", subject: str = "", text: str = "", days: int = 0,
               limit: int = 10) -> list[dict]:
        criteria = []
        if unread:
            criteria.append("UNSEEN")
        for key, value in (("FROM", sender), ("SUBJECT", subject), ("TEXT", text)):
            value = re.sub(r'["\\\r\n]', " ", value or "").strip()[:100]
            if value:
                criteria += [key, f'"{value}"']
        if days:
            criteria += ["SINCE", time.strftime("%d-%b-%Y", time.localtime(time.time() - days * 86400))]
        box = self._open()
        try:
            status, data = box.uid("search", None, *(criteria or ["ALL"]))
            if status != "OK":
                raise EmailError("The search didn't work.")
            uids = (data[0] or b"").split()[-limit:][::-1]       # newest first
            return self._fetch(box, uids)
        finally:
            try:
                box.logout()
            except Exception:
                pass

    def read(self, message_id: str) -> dict | None:
        if not str(message_id).isdigit():
            raise EmailError("That isn't an email id.")
        box = self._open()
        try:
            found = self._fetch(box, [str(message_id).encode()])
            return found[0] if found else None
        finally:
            try:
                box.logout()
            except Exception:
                pass

    # -- sending (only after approval) ------------------------------------------------------
    def send(self, to: str, subject: str, body: str) -> str:
        recipients = [a.strip() for a in re.split(r"[,;]", to or "") if a.strip()]
        if not recipients or not all(ADDRESS.fullmatch(a) for a in recipients) or len(recipients) > 20:
            raise EmailError("Give one or more valid email addresses (up to 20).")
        msg = EmailMessage()
        msg["From"], msg["To"], msg["Subject"] = self.address, ", ".join(recipients), (subject or "")[:300]
        msg["Date"] = email.utils.formatdate(localtime=True)
        msg.set_content((body or "")[:50_000])
        try:
            server = self._smtp_factory(self.smtp_host)
            server.starttls(context=ssl.create_default_context())
            server.login(self.address, self.password)
            server.send_message(msg)
            server.quit()
        except smtplib.SMTPAuthenticationError as e:
            raise EmailError("The email provider refused the login. Check the app password in Settings.") from e
        except (smtplib.SMTPException, OSError) as e:
            raise EmailError(f"Sending failed ({type(e).__name__}).") from e
        return f"Sent to {', '.join(recipients)}: {subject}"
