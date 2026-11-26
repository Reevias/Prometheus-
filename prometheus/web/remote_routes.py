"""Routes for controlling the laptop from the phone (M6 6.4b): the phone's API (device key) and the owner's buttons."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from prometheus.remote_control import RemoteError, private_link_ok


def register_remote_routes(app: FastAPI, services) -> None:
    def rc():
        return services.remote

    def guard(request: Request, need_key: bool = True):
        """Private link -> switch -> Stop all -> device key. Returns (device, None) or (None, refusal response)."""
        host = request.client.host if request.client else ""
        path = request.url.path
        if not private_link_ok(host, request.headers):
            rc().refused("not the private link", host, path)
            return None, JSONResponse({"error": "Only over the private link."}, status_code=403)
        if not rc().enabled():
            rc().refused("switched off", host, path)
            return None, JSONResponse({"error": "Control from the phone is switched off on the laptop."}, status_code=403)
        if rc().stopped():
            rc().refused("stopped", host, path)
            return None, JSONResponse({"error": "Everything is stopped on the laptop."}, status_code=423)
        if not need_key:
            return None, None
        if not rc().limiter.attempt(host):
            rc().refused("too many tries", host, path)
            return None, JSONResponse({"error": "Too many tries. Wait 5 minutes."}, status_code=429)
        sent = request.headers.get("Authorization", "").removeprefix("Bearer ").strip() or request.headers.get("X-Device-Key", "")
        device = rc().authenticate(sent)
        if device is None:
            rc().refused("unknown key", host, path)
            return None, JSONResponse({"error": "This phone isn't paired."}, status_code=401)
        rc().limiter.reset(host)
        return device, None

    def answer(call):
        try:
            return call()
        except RemoteError as e:
            return JSONResponse({"error": e.message}, status_code=e.status)

    async def body(request: Request) -> dict:
        try:
            data = await request.json()
        except ValueError:
            return {}
        return data if isinstance(data, dict) else {}

    @app.post("/api/remote/pair")
    async def pair(request: Request):
        _, refusal = guard(request, need_key=False)
        if refusal:
            return refusal
        data = await body(request)
        try:
            return rc().pair(str(data.get("code", "")), str(data.get("name", "")))
        except RemoteError as e:
            rc().refused("bad pairing code", request.client.host if request.client else "", request.url.path)
            return JSONResponse({"error": e.message}, status_code=e.status)

    @app.get("/api/remote/actions")
    def actions(request: Request):
        device, refusal = guard(request)
        return refusal or {"actions": rc().catalog()}

    @app.post("/api/remote/run")
    async def run(request: Request):
        device, refusal = guard(request)
        if refusal:
            return refusal
        data = await body(request)
        return answer(lambda: rc().propose(device, str(data.get("action", "")), data.get("params") or {}))

    @app.post("/api/remote/requests/{request_id}/confirm")
    async def confirm(request: Request, request_id: str):
        device, refusal = guard(request)
        if refusal:
            return refusal
        data = await body(request)
        return answer(lambda: rc().confirm(device, request_id, str(data.get("confirm", ""))))

    @app.post("/api/remote/requests/{request_id}/cancel")
    def cancel(request: Request, request_id: str):
        device, refusal = guard(request)
        return refusal or answer(lambda: rc().cancel(device, request_id))

    @app.get("/api/remote/requests/{request_id}")
    def status(request: Request, request_id: str):
        device, refusal = guard(request)
        return refusal or answer(lambda: rc().status(device, request_id))

    # -- the owner's buttons (browser login and CSRF, like every other page) --------------------------------------------
    @app.post("/api/v3/remote/pairing-code")
    def pairing_code():
        return answer(lambda: rc().new_pairing_code())

    @app.post("/api/v3/remote/devices/{device_id}/revoke")
    def revoke(device_id: str):
        if not rc().revoke(device_id):
            return JSONResponse({"error": "No such device."}, status_code=404)
        return {"revoked": device_id}
