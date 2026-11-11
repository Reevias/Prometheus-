import base64
import json
from email.message import EmailMessage

import httpx

from prometheus.connectors.gmail import GmailBox, _query
from prometheus.connectors.google import Google
from tests.conftest import login


def raw_mail(subject, body, sender="Ravi <ravi@example.com>"):
    m = EmailMessage()
    m["From"], m["Subject"], m["Date"] = sender, subject, "Wed, 11 Nov 2026 10:00:00 +0530"
    m.set_content(body)
    return base64.urlsafe_b64encode(m.as_bytes()).decode()


def gmail(handler):
    g = Google(httpx.Client(transport=httpx.MockTransport(handler)))
    g._access, g._until = "token", 9e18
    return GmailBox(g)


def test_search_read_send(tmp_home):
    sent = []

    def handler(r):
        path = r.url.path
        if path.endswith("/profile"):
            return httpx.Response(200, json={"emailAddress": "me@gmail.com"})
        if path.endswith("/messages") and r.method == "GET":
            assert "is:unread" in r.url.params["q"]
            return httpx.Response(200, json={"messages": [{"id": "18c2f0a1b2"}]})
        if path.endswith("/messages/18c2f0a1b2"):
            return httpx.Response(200, json={"raw": raw_mail("Lunch?", "Free at 1pm?")})
        if path.endswith("/messages/send"):
            sent.append(json.loads(r.content))
            return httpx.Response(200, json={"id": "x"})
        return httpx.Response(404)

    box = gmail(handler)
    found = box.search(unread=True)
    assert found[0]["subject"] == "Lunch?" and "Free at 1pm" in found[0]["body"] and found[0]["from"].startswith("Ravi")
    assert box.read("18c2f0a1b2")["id"] == "18c2f0a1b2"
    assert "Sent to ravi@example.com" in box.send("ravi@example.com", "Re: Lunch", "Yes!")
    msg = base64.urlsafe_b64decode(sent[0]["raw"]).decode()
    assert "From: me@gmail.com" in msg and "Yes!" in msg


def test_query_and_missing_permission(tmp_home):
    assert _query(False, 'evil" OR x', "", "", 3) == 'from:"evil  OR x" newer_than:3d'
    box = gmail(lambda r: httpx.Response(403))
    try:
        box.search()
    except Exception as e:
        assert "Sign in with Google" in str(e)


def test_settings_help_and_buttons(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    assert "Sign in with Google" in client.get("/settings/email/help?address=me@gmail.com").text
    assert "outlook" in client.get("/settings/email/help?address=me@hotmail.com").text.lower()
    r = client.post("/settings/email/google", headers=h)
    assert r.status_code == 400 and "Sign in with Google first" in r.text
    page = client.get("/settings").text
    assert 'id="google-signin"' in page and "set-nav" in page
