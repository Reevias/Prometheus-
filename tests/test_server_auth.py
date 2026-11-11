import re

from conftest import login

from prometheus.auth import hash_password, verify_password


def test_password_hash_verify():
    stored = hash_password("s3cret-password")
    assert stored.startswith("scrypt$")
    assert verify_password("s3cret-password", stored)
    assert not verify_password("wrong", stored)
    assert not verify_password("x", "")


def test_every_route_requires_login(app_client):
    client, _ = app_client()
    app = client.app
    for route in app.routes:
        path = getattr(route, "path", "")
        if path in ("/login",) or path.startswith("/static"):
            continue
        if path == "/google/callback":  # Google sends the owner back here; without the one-time state it refuses
            assert client.get("/google/callback?code=x&state=y").status_code == 400
            continue
        if path == "/hooks/{token}":    # open on purpose for other apps; a wrong secret gets "not found"
            assert client.post("/hooks/wrong", json={}).status_code == 404
            continue
        if path == "/healthz":          # open on purpose, so a second window finds the running copy
            assert client.get(path).json() == {"app": "prometheus"}
            continue
        url = re.sub(r"{[^}]+}", "1", path)
        for method in getattr(route, "methods", set()) - {"HEAD", "OPTIONS"}:
            resp = client.request(method, url, follow_redirects=False)
            assert resp.status_code in (303, 401), f"{method} {path} -> {resp.status_code}"


def test_login_then_health(app_client):
    client, _ = app_client()
    login(client)
    resp = client.get("/health")
    assert resp.status_code == 200 and resp.json()["ok"] is True


def test_login_wrong_password_rate_limited(app_client):
    client, _ = app_client()
    codes = [client.post("/login", data={"password": "nope"}, follow_redirects=False).status_code
             for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429
    assert client.post("/login", data={"password": "correct horse battery"},
                       follow_redirects=False).status_code == 429


def test_post_without_csrf_rejected(app_client):
    client, _ = app_client()
    token = login(client)
    assert client.post("/logout", follow_redirects=False).status_code == 403
    assert client.post("/logout", headers={"X-CSRF-Token": "wrong"}, follow_redirects=False).status_code == 403
    assert client.post("/logout", headers={"X-CSRF-Token": token}, follow_redirects=False).status_code == 303
    assert client.get("/health", follow_redirects=False).status_code in (303, 401)


def test_session_cookie_flags(app_client):
    client, _ = app_client()
    resp = client.post("/login", data={"password": "correct horse battery"}, follow_redirects=False)
    cookie = resp.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie


def test_secrets_never_in_responses(app_client, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-123")
    client, _ = app_client()
    login(client)
    for url in ("/health", "/login"):
        assert "sk-test-123" not in client.get(url).text


def test_build_services_marks_interrupted(app_client):
    client, services = app_client()
    chat = services.store.create_chat("c")
    t = services.store.create_task(chat, "x")
    services.store.set_task_status(t, "running")
    from prometheus.app import build_services
    build_services(services.settings, providers=services.router.providers, home=services.home)
    assert services.store.task(t)["status"] == "interrupted"
