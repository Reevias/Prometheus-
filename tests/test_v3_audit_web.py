"""Security audit (16 Nov): login limiter behind the cloud proxy, cookie flag, escaped replies."""
from __future__ import annotations

from fastapi.testclient import TestClient

from prometheus.app import build_services
from prometheus.auth import hash_password
from prometheus.config import Settings
from prometheus.models.fake import FakeProvider
from prometheus.server import create_app


def client_for(tmp_home):
    s = Settings(password_hash=hash_password("correct horse battery"), routes={"plan": ["fake"]})
    services = build_services(s, providers={"fake": FakeProvider(['{"steps": []}'], name="fake")}, home=tmp_home.home)
    # behind `tailscale serve` every request comes from the proxy on 127.0.0.1
    return TestClient(create_app(services), base_url="http://127.0.0.1:8000", client=("127.0.0.1", 50000))


def test_one_device_guessing_does_not_lock_out_the_others(tmp_home):
    c = client_for(tmp_home)
    bad = {"X-Forwarded-For": "100.64.0.9"}
    for _ in range(6):
        c.post("/login", data={"password": "wrong"}, headers=bad)
    assert c.post("/login", data={"password": "wrong"}, headers=bad).status_code == 429
    ok = c.post("/login", data={"password": "correct horse battery"}, headers={"X-Forwarded-For": "100.64.0.5"},
                follow_redirects=False)
    assert ok.status_code == 303                        # another device is not locked out


def test_cookies_are_secure_when_the_proxy_says_https(tmp_home):
    c = client_for(tmp_home)
    r = c.post("/login", data={"password": "correct horse battery"}, headers={"X-Forwarded-Proto": "https"},
               follow_redirects=False)
    cookies = r.headers.get_list("set-cookie")
    assert cookies and all("secure" in v.lower() for v in cookies)
    r = c.post("/login", data={"password": "correct horse battery"}, follow_redirects=False)   # plain local http
    assert not any("secure" in v.lower() for v in r.headers.get_list("set-cookie"))
