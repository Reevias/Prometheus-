"""FastAPI app: login wall, CSRF check, and the routes."""
from __future__ import annotations

import html
import hmac
import json
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from prometheus.app import Services
from prometheus.auth import LoginLimiter, Sessions, verify_password

SESSION_COOKIE = "prom_session"
CSRF_COOKIE = "csrf_token"
OPEN_PATHS = ("/login", "/healthz", "/google/callback")
APP_ID = "prometheus"


def _login_page(message: str = "") -> str:
    note = f'<p class="error">{html.escape(message)}</p>' if message else ""
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'><title>Prometheus</title>"
            "<script>try{var t=localStorage.getItem('prom-theme');if(t)document.documentElement.dataset.theme=t}catch(e){}</script>"
            "<link rel='stylesheet' href='/static/style.css'></head><body class='login'>"
            "<canvas class='orb orb-login' data-orb data-state='idle' aria-hidden='true'></canvas>"
            "<form method='post' action='/login'><h1>Prometheus</h1>" + note +
            "<label>Password <input type='password' name='password' autofocus required></label>"
            "<button type='submit'>Log in</button></form>"
            "<script src='/static/orb.js'></script></body></html>")


def describe_hook(github_event: str, data: dict) -> tuple[str, str]:
    """A short sentence from a webhook: GitHub test runs and pushes are understood, anything else shown as text."""
    data = data if isinstance(data, dict) else {"text": str(data)}
    repo = str((data.get("repository") or {}).get("full_name", "")) if isinstance(data.get("repository"), dict) else ""
    if github_event == "workflow_run":
        run = data.get("workflow_run") or {}
        if data.get("action") != "completed":
            return "GitHub", f"{run.get('name', 'A check')} started on {repo}."
        verdict = {"success": "passed", "failure": "failed", "cancelled": "was cancelled"}.get(run.get("conclusion"),
                                                                                            run.get("conclusion", "ended"))
        return "GitHub", f"{run.get('name', 'Tests')} {verdict} on {repo} ({run.get('head_branch', '')})."
    if github_event == "push":
        return "GitHub", f"New push to {repo}: {str((data.get('head_commit') or {}).get('message', ''))[:120]}"
    if github_event:
        return "GitHub", f"{github_event.replace('_', ' ')} on {repo}."
    for key in ("text", "message", "title", "content"):
        if data.get(key):
            return str(data.get("source") or "Webhook")[:40], str(data[key])
    return "Webhook", json.dumps(data)[:300]


def create_app(services: Services, background: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        if services.voice is not None:          # listening for "Prometheus" lives as long as the app
            services.voice.start_if_enabled()
        if services.daily is not None:          # end of each day: repo of the day, then updating
            services.daily.start(background=background)
        if services.reminders is not None:
            services.reminders.start()
        if services.watchers is not None:
            services.watchers.start()
        if services.briefing is not None:
            services.briefing.start()
        yield
        if services.watchers is not None:
            services.watchers.stop()
        if services.reminders is not None:
            services.reminders.stop()
        if services.daily is not None:
            services.daily.stop()
        if services.voice is not None:
            services.voice.stop()
        if services.browser is not None:
            services.browser.close()

    app = FastAPI(title="Prometheus", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.services = services
    sessions, limiter = Sessions(), LoginLimiter()
    app.state.sessions = sessions
    app.state.limiter = limiter

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path in OPEN_PATHS or path.startswith(("/static/", "/hooks/")):    # hooks check their own secret
            return await call_next(request)
        csrf = sessions.get(request.cookies.get(SESSION_COOKIE))
        if csrf is None:
            if request.headers.get("HX-Request") or request.method != "GET":
                return Response(status_code=401)
            return RedirectResponse("/login", status_code=303)
        if request.method not in ("GET", "HEAD"):
            sent = request.headers.get("X-CSRF-Token", "")
            if not hmac.compare_digest(sent, csrf):
                return Response("CSRF check failed", status_code=403)
        request.state.csrf = csrf
        return await call_next(request)

    @app.get("/login", response_class=HTMLResponse)
    def login_form():
        return _login_page()

    @app.post("/login")
    def login(request: Request, password: str = Form("")):
        client = request.client.host if request.client else "unknown"
        if not limiter.attempt(client):
            return HTMLResponse(_login_page("Too many tries. Wait 5 minutes."), status_code=429)
        if not services.settings.password_hash or not verify_password(password, services.settings.password_hash):
            return HTMLResponse(_login_page("Wrong password."), status_code=401)
        limiter.reset(client)
        sid, csrf = sessions.create()
        resp = RedirectResponse("/", status_code=303)
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", max_age=12 * 3600)
        resp.set_cookie(CSRF_COOKIE, csrf, httponly=False, samesite="strict", max_age=12 * 3600)
        services.store.audit("user", "login", {"client": client})
        return resp

    @app.post("/logout")
    def logout(request: Request):
        sessions.drop(request.cookies.get(SESSION_COOKIE))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(SESSION_COOKIE)
        resp.delete_cookie(CSRF_COOKIE)
        return resp

    @app.post("/hooks/{token}")
    async def webhook(request: Request, token: str):
        """Other apps call this to make Prometheus speak up. The token in the address is the password."""
        expected = services.settings.webhook_token
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return Response(status_code=404)
        body = (await request.body())[:65536]
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {"text": body.decode("utf-8", errors="replace")}
        source, text = describe_hook(request.headers.get("X-GitHub-Event", ""), data)
        if services.watchers is not None:
            services.watchers.webhook(source, text)
        services.store.audit("webhook", "received", {"source": source})
        return JSONResponse({"ok": True})

    @app.get("/healthz")
    def healthz():
        # Open, so a second window can find this copy. Says nothing private.
        return JSONResponse({"app": APP_ID})

    @app.get("/health")
    def health():
        return JSONResponse({"ok": True, "models": services.router.status(),
                             "budget": services.budget.state().__dict__, "stopped": services.killswitch.is_set})

    # Refuse requests whose Host header isn't this laptop (blocks DNS-rebinding pages in the browser).
    allowed = ["*"] if services.settings.allow_lan else ["127.0.0.1", "localhost", "[::1]"]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed)

    from prometheus.web.routes import register_routes

    register_routes(app, services)
    return app
