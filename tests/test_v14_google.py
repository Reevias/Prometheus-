import json
from urllib.parse import parse_qs, urlsplit

import httpx

import prometheus.agents.google_agent as ga
from prometheus.connectors.google import Google, finish_sign_in, start_sign_in
from tests.conftest import login


class FakeGoogle:
    def __init__(self):
        self.added = []

    def events(self, start, end, query=""):
        return [{"id": "e1", "title": "Dentist", "start": "2026-11-12T17:00:00+05:30", "end": "", "location": "Clinic"}]

    def add_event(self, title, start, minutes=60, location=""):
        self.added.append((title, minutes))
        return {}

    def move_event(self, event_id, start, minutes=60):
        self.added.append(("moved", event_id))
        return {}

    def drive_search(self, query, limit=15):
        return [{"id": "d1", "name": "Physics notes", "modifiedTime": "2026-11-01T10:00:00Z"}]

    def drive_read(self, file_id):
        return "Physics notes", "Newton's laws..."


def test_sign_in_uses_pkce_and_keeps_only_a_refresh_token(monkeypatch, tmp_home):
    from prometheus.config import get_secret, set_secret

    set_secret("GOOGLE_CLIENT", "123-abc.apps.googleusercontent.com|GOCSPX-secret123")
    url, pending = start_sign_in("http://127.0.0.1:8000/google/callback")
    q = parse_qs(urlsplit(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["state"] == [pending["state"]] and "drive.readonly" in q["scope"][0]
    seen = []

    def token(request):
        seen.append(dict(parse_qs(request.content.decode())))
        if "code" in seen[-1]:
            return httpx.Response(200, json={"access_token": "a1", "refresh_token": "1//r1-refresh-token", "expires_in": 3600})
        return httpx.Response(200, json={"access_token": "a2", "expires_in": 3600})

    finish_sign_in("thecode", pending, httpx.Client(transport=httpx.MockTransport(token)))
    assert get_secret("GOOGLE_TOKEN") == "1//r1-refresh-token" and seen[0]["code_verifier"] == [pending["verifier"]]

    calls = []

    def api(request):
        if request.url.host == "oauth2.googleapis.com":
            return token(request)
        calls.append((request.method, request.url.path, request.headers["authorization"]))
        return httpx.Response(200, json={"items": [], "files": []})

    g = Google(httpx.Client(transport=httpx.MockTransport(api)))
    g.events(0, 100)
    g.add_event("Study", 1_800_000_000, 90)
    assert calls[0][2] == "Bearer a2" and calls[1][0] == "POST"


def test_agent_reads_and_asks_before_adding(make_ctx, monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(ga, "CLIENT", fake)
    ctx = make_ctx()
    ctx.approvals.register("google_event", ga.event_handler)
    assert "Dentist" in ga.GoogleAgent().run("x", {"action": "events", "when": "tomorrow"}, ctx).summary
    assert "Physics notes" in ga.GoogleAgent().run("x", {"action": "drive_search", "query": "physics"}, ctx).summary
    assert "Newton" in ga.GoogleAgent().run("x", {"action": "drive_read", "id": "d1"}, ctx).summary
    res = ga.GoogleAgent().run("x", {"action": "add_event", "title": "Study physics", "when": "tomorrow at 5pm",
                                     "minutes": "90"}, ctx)
    assert res.status == "waiting_approval" and fake.added == []                 # nothing added before a yes
    ask = ctx.store.asks_for_chat(ctx.chat_id())[0]
    assert "Study physics" in ctx.approvals.resolve(ask.id, approved=True) and fake.added == [("Study physics", 90)]


def test_settings_and_callback_need_the_right_state(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    pw = "correct horse battery"
    bad = client.post("/settings/google", data={"client_id": "nope", "client_secret": "x", "password": pw}, headers=h)
    assert bad.status_code == 400
    ok = client.post("/settings/google", data={"client_id": "123-abc.apps.googleusercontent.com",
                                               "client_secret": "GOCSPX-secret123", "password": pw}, headers=h)
    assert ok.status_code == 200 and "Sign in with Google" in ok.text
    assert client.get("/google/callback?code=x&state=forged").status_code == 400
