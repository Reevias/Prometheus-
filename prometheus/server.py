"""FastAPI app: login wall, CSRF check, and the routes."""
from __future__ import annotations

import html
import hmac
from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from prometheus.app import Services
from prometheus.auth import LoginLimiter, Sessions, verify_password

SESSION_COOKIE = "prom_session"
CSRF_COOKIE = "csrf_token"
OPEN_PATHS = ("/login", "/healthz")
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


def create_app(services: Services, background: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        if services.voice is not None:          # listening for "Prometheus" lives as long as the app
            services.voice.start_if_enabled()
        if services.updater is not None:
            services.updater.start(background=background)
        yield
        if services.updater is not None:
            services.updater.stop()
        if services.voice is not None:
            services.voice.stop()

    app = FastAPI(title="Prometheus", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.services = services
    sessions, limiter = Sessions(), LoginLimiter()
    app.state.sessions = sessions
    app.state.limiter = limiter

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path in OPEN_PATHS or path.startswith("/static/"):
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
