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
OPEN_PATHS = ("/login", "/healthz", "/google/callback", "/manifest.webmanifest", "/sw.js")
MANIFEST = {"name": "Prometheus", "short_name": "Prometheus", "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#0e0f11", "theme_color": "#0e0f11", "description": "Your own AI assistant",
            "icons": [{"src": "/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                      {"src": "/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                      {"src": "/static/icon-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"}]}
APP_ID = "prometheus"


def _login_page(message: str = "") -> str:
    note = f'<p class="error">{html.escape(message)}</p>' if message else ""
    return ("<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width, initial-scale=1'><title>Prometheus</title><link rel='icon' type='image/png' href='/static/favicon.png'><link rel='manifest' href='/manifest.webmanifest'><meta name='theme-color' content='#0e0f11'><link rel='apple-touch-icon' href='/static/icon-192.png'>"
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


def _say_whats_new(services) -> None:
    """Just restarted after an update: say which version this is and what's new, once."""
    store = services.store
    if not store.kv_get("just_updated"):
        return
    store.kv_set("just_updated", "")
    from prometheus.launcher import CODE_DIR, version

    news = []
    try:
        lines = (CODE_DIR / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
        start = next(i for i, l in enumerate(lines) if l.startswith("## "))
        for l in lines[start + 1:]:
            if l.startswith("## "):
                break
            if l.startswith("- "):
                news.append(l[2:].split(":")[0][:90])
    except (OSError, StopIteration):
        pass
    text = f"Prometheus updated itself to version {version()}." + (
        " What's new: " + "; ".join(news[:6]) + "." if news else "")
    try:
        chat = services.watchers.alerts_chat() if services.watchers is not None else store.create_chat("Prometheus alerts")
        store.add_message(chat, "assistant", text, "update")
        services.notify("update", text[:200])
    except Exception:
        pass


def create_app(services: Services, background: bool = False) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app):
        if services.voice is not None:          # listening for "Prometheus" lives as long as the app
            services.voice.start_if_enabled()
        _say_whats_new(services)
        if services.daily is not None:          # end of each day: repo of the day, then updating
            services.daily.start(background=background)
        if services.reminders is not None:
            services.reminders.start()
        if services.watchers is not None:
            services.watchers.start()
        if services.briefing is not None:
            services.briefing.start()
        cloud = getattr(services.settings, "role", "laptop") == "cloud"
        if services.file_index is not None and not cloud:      # the cloud copy has no files, photos or clipboard
            services.file_index.start()
        if services.photos is not None and not cloud:
            services.photos.start()
        if services.clips is not None and not cloud:
            services.clips.start()
        if services.sync is not None and not cloud:
            services.sync.start()
        if getattr(services, "laptop_jobs", None) is not None and not cloud:
            services.laptop_jobs.start()
        if services.photos is not None and cloud:
            services.photos.start()            # on the cloud copy: only photos sent from the phone
        if services.focus is not None:
            services.focus.start_clock()
        if getattr(services, "resumer", None) is not None:
            services.resumer.start()
        yield
        if getattr(services, "resumer", None) is not None:
            services.resumer.stop()
        if services.focus is not None:
            services.focus.stop_clock()
        if services.clips is not None:
            services.clips.stop()
        if services.file_index is not None:
            services.file_index.stop()
        if services.photos is not None:
            services.photos.stop()
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
        if path in OPEN_PATHS or path.startswith(("/static/", "/hooks/")) or path in (
                "/api/mcp", "/api/sync", "/api/notify", "/api/laptop-jobs"):
            # hooks and the tool server check their own secret
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

    @app.get("/manifest.webmanifest")
    def manifest():
        return Response(json.dumps(MANIFEST), media_type="application/manifest+json")

    @app.get("/sw.js")
    def service_worker():
        from pathlib import Path as _P

        body = (_P(__file__).resolve().parent / "web" / "static" / "sw.js").read_text(encoding="utf-8")
        return Response(body, media_type="application/javascript", headers={"Service-Worker-Allowed": "/"})

    def _sync_key_ok(request: Request) -> bool:
        from prometheus.sync import token

        sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        expected = token(create=False)
        local = (request.client.host if request.client else "") in ("127.0.0.1", "::1", "testclient")
        return bool(expected) and local and hmac.compare_digest(sent.encode(), expected.encode())

    @app.post("/api/notify")
    async def notify_endpoint(request: Request):
        """The laptop handing a notification to the cloud copy, which sends it to the phone."""
        import asyncio

        from prometheus.phone import push

        if not _sync_key_ok(request):
            return Response(status_code=401)
        try:
            body = json.loads((await request.body())[:20000] or b"{}")
        except ValueError:
            return Response(status_code=400)
        n = await asyncio.to_thread(push, services.store, str(body.get("title") or "Prometheus"),
                                    str(body.get("text") or ""), str(body.get("url") or "/"))
        return Response(json.dumps({"sent": n}), media_type="application/json")

    @app.post("/api/laptop-jobs")
    async def laptop_jobs(request: Request):
        """The laptop picking up what the phone asked for that only the laptop can do."""
        import time as _t

        from prometheus.phone import take_jobs

        if not _sync_key_ok(request):
            return Response(status_code=401)
        services.store.kv_set("laptop_seen", _t.time())
        return Response(json.dumps({"jobs": take_jobs(services.store)}), media_type="application/json")

    @app.post("/hooks/{token}/sms")
    async def sms_hook(request: Request, token: str):
        """Bank SMS forwarded from the phone (MacroDroid or another SMS forwarder) into the money tracker."""
        from prometheus.agents import money_agent
        from prometheus.phone import sms_to_money

        expected = services.settings.webhook_token
        if not expected or not hmac.compare_digest(token.encode(), expected.encode()):
            return Response(status_code=404)
        raw = (await request.body())[:20000]
        try:
            body = json.loads(raw or b"{}")
        except ValueError:
            body = {"text": raw.decode("utf-8", errors="replace")}
        if money_agent.MONEY is None:
            return Response("Money tracker is off.", status_code=503)
        said = sms_to_money(money_agent.MONEY, str(body.get("from") or body.get("sender") or ""),
                            str(body.get("text") or body.get("message") or body.get("body") or ""))
        return Response(said)

    @app.post("/api/sync")
    async def sync_endpoint(request: Request):
        """The laptop and the cloud copy swapping changes. Only with the sync key, only through the private link."""
        import asyncio
        import time as _t

        from prometheus.sync import export, merge, token

        sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        expected = token(create=False)
        local = (request.client.host if request.client else "") in ("127.0.0.1", "::1", "testclient")
        if not expected or not local or not hmac.compare_digest(sent.encode(), expected.encode()):
            return Response(status_code=401)
        try:
            body = json.loads((await request.body())[:50_000_000] or b"{}")
        except ValueError:
            return Response(status_code=400)
        since = float(body.get("since") or 0)
        got = await asyncio.to_thread(merge, services.store, body.get("data") or {})
        services.store.kv_set("laptop_seen", _t.time())
        back = await asyncio.to_thread(export, services.store, since)
        return Response(json.dumps({"data": back, "got": got}), media_type="application/json")

    @app.post("/api/mcp")
    async def mcp_endpoint(request: Request):
        """Prometheus as a tool for other AI apps. Only callers with the token (and only from this laptop)."""
        from prometheus.mcp_server import PrometheusMCP, token

        sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        expected = token(create=False)
        local = (request.client.host if request.client else "") in ("127.0.0.1", "::1", "testclient")
        if not expected or not local or not hmac.compare_digest(sent.encode(), expected.encode()):
            return Response(status_code=401)
        try:
            msg = json.loads((await request.body())[:200_000] or b"{}")
        except ValueError:
            return Response(status_code=400)
        server = PrometheusMCP(services)
        import asyncio

        if isinstance(msg, list):
            replies = [r for r in [await asyncio.to_thread(server.handle, m) for m in msg] if r]
            return Response(json.dumps(replies), media_type="application/json") if replies else Response(status_code=202)
        reply = await asyncio.to_thread(server.handle, msg)
        if reply is None:
            return Response(status_code=202)
        return Response(json.dumps(reply), media_type="application/json")

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
