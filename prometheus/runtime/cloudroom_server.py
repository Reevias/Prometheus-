"""The Cloudroom server (M6 step 6.2, item 5): runs on the owner's free Render service and runs moved tasks.

- Every request but /v1/health must be signed with the Cloudroom token (cloudroom_protocol); the pinger uses health.
- A task arrives with the laptop's request id: the same id is never run twice; it gets a signed receipt.
- One task at a time (the free machine has 512 MB), each in Prometheus's sandbox with a time and a memory limit, no
  internet, an almost empty environment, and no way to read the token: the token's folders and /proc are added to the
  sandbox's private places, and the token is never in a task's environment.
- Events are numbered per task. Everything lives in memory: Render can restart at any time and forget it, which the
  laptop sees (a new boot id) and handles without ever sending the task again.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from prometheus.runtime import cloudroom_protocol as proto

MAX_CODE = 200_000                 # characters of code a task may bring
MAX_UPLOAD = 5 * 1024 * 1024       # bytes of files a task may bring
MAX_SECONDS = 300
MAX_MEMORY_MB = 256                # half the free machine; the server itself needs the rest
MAX_RESULT_FILES = 1024 * 1024
MAX_BODY = 8 * 1024 * 1024         # the most a request may send (5 MB of files as base64, plus the code)
KEEP_TASKS = 50                    # finished tasks kept for the laptop to read; older ones are let go (512 MB)
KEEP_RECEIPTS = 5000               # request ids remembered, so a late repeat is still never run twice
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,99}$")


def task_digest(task: dict) -> str:
    """What exactly was asked for (code, files, limits): the receipt and the owner's yes are tied to it."""
    keep = {k: task.get(k) for k in ("request_id", "kind", "code", "files", "seconds", "memory_mb", "network")}
    return hashlib.sha256(json.dumps(keep, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def harden_process() -> None:
    """Make this process not dumpable (Linux): other processes of the same user, such as a task that got out of the
    sandbox, can't read its memory or /proc/<pid>/environ, where the token may be (M6 audit A1)."""
    if sys.platform.startswith("linux"):
        try:
            import ctypes

            ctypes.CDLL(None).prctl(4, 0, 0, 0, 0)                  # PR_SET_DUMPABLE = 4
        except Exception:                                           # noqa: BLE001 - the other walls still stand
            pass


def sandbox_runner(base: Path, secret_paths: list[str]) -> Callable[[dict], dict]:
    """Runs a task in Prometheus's sandbox, with the token's folders and /proc private too."""
    from prometheus import sandbox

    original = sandbox._private_places
    extra = [p for p in [*secret_paths, "/proc"] if p]

    def private(folder):                                   # this process only runs Cloudroom
        return list(original(folder)) + extra

    sandbox._private_places = private

    def run(task: dict) -> dict:
        folder = sandbox.new_folder(base)
        for name, data in (task.get("files") or {}).items():
            (folder / name).write_bytes(base64.b64decode(data))
        try:
            # no new process or thread may start from the task, by any route (the kernel refuses; M6 audit A1)
            res = sandbox.run_locked(task["code"], folder, network=False, timeout=int(task["seconds"]),
                                     memory_mb=int(task["memory_mb"]), no_new_processes=True,
                                     should_stop=task.get("should_stop"))   # Cancel ends the program at once
            files, size = {}, 0
            for p in res.files:
                n = p.stat().st_size                        # checked before reading (M6 audit A5)
                if size + n > MAX_RESULT_FILES:
                    continue
                size += n
                files[p.name] = base64.b64encode(p.read_bytes()).decode()
            return {"ok": res.ok, "output": res.output, "timed_out": res.timed_out, "files": files}
        finally:
            shutil.rmtree(folder, ignore_errors=True)       # nothing of a task stays on the disk

    return run


def create_app(token: str, base: Path | str, runner: Callable[[dict], dict] | None = None,
               secret_paths: list[str] | None = None) -> FastAPI:
    """The Cloudroom web app. `token` comes from Render's secret (never from a task)."""
    if not token or len(token) < 24:
        raise ValueError("The Cloudroom token must be at least 24 characters.")
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)
    run = runner or sandbox_runner(base, secret_paths or [])
    boot_id = secrets.token_hex(8)                         # changes on every restart
    lock = threading.RLock()
    tasks: dict[str, dict] = {}
    by_request: dict[str, dict] = {}                       # request id -> its receipt (small; kept after the task goes)
    nonces: dict[str, float] = {}
    work: queue.Queue = queue.Queue()
    app = FastAPI(title="Prometheus Cloudroom", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.cloudroom = {"tasks": tasks, "by_request": by_request, "boot_id": boot_id}

    def seen(nonce: str) -> bool:
        now = time.time()
        with lock:
            for n, t in list(nonces.items()):
                if now - t > 2 * proto.MAX_SKEW:
                    del nonces[n]
            if nonce in nonces:
                return False
            nonces[nonce] = now
            return True

    async def authorised(request: Request) -> bytes | None:
        size = request.headers.get("content-length", "")
        if (size.isdigit() and int(size) > MAX_BODY) or not request.headers.get(proto.H_SIG):
            return None                                    # refused before the body is read (M6 audit A5)
        body = b""
        async for piece in request.stream():               # read with a cap, also when no length was given
            body += piece
            if len(body) > MAX_BODY:
                return None
        
        path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        ok = proto.check(token, request.method, path, body, request.headers, seen)
        return body if ok else None

    def denied():
        return JSONResponse({"error": "not signed by the owner's Prometheus"}, status_code=401)

    def event(task: dict, state: str, text: str = "") -> None:
        task["events"].append({"seq": len(task["events"]) + 1, "at": time.time(), "state": state, "text": text[:500]})
        task["state"] = state

    def worker() -> None:
        while True:
            task = work.get()
            with lock:
                if task["state"] == "cancelled":
                    task["files"] = {}
                    continue
                event(task, "running")
                task["should_stop"] = lambda t=task: t["state"] == "cancelled"
            try:
                result = run(task)
            except Exception as e:                         # noqa: BLE001 - a broken task is its own result
                result = {"ok": False, "output": f"{type(e).__name__}: {e}"[:2000], "files": {}}
            with lock:
                task["files"] = {}                         # the uploads aren't needed any more (512 MB)
                if task["state"] == "cancelled":
                    continue                               # cancelled while it ran: the result is dropped
                task["result"] = result
                event(task, "done" if result.get("ok") else "failed", "" if result.get("ok") else
                      str(result.get("output", ""))[:300])

    threading.Thread(target=worker, name="cloudroom-worker", daemon=True).start()

    @app.get("/v1/health")
    def health():
        return {"ok": True}

    @app.post("/v1/tasks")
    async def submit(request: Request):
        size = request.headers.get("content-length", "")
        if size.isdigit() and int(size) > MAX_BODY:
            return JSONResponse({"error": "too big"}, status_code=413)
        body = await authorised(request)
        if body is None:
            return denied()
        try:
            data = json.loads(body)
            rid, kind, code = str(data["request_id"]), data.get("kind"), data.get("code")
        except (ValueError, KeyError, TypeError):
            return JSONResponse({"error": "not a task"}, status_code=400)
        with lock:
            if rid in by_request:                          # the same request again: the same receipt, not run again
                return by_request[rid]
        files = data.get("files") or {}
        seconds, memory = int(data.get("seconds") or 60), int(data.get("memory_mb") or 128)
        problem = ("only Python tasks run here" if kind != "python" else
                   "no code" if not isinstance(code, str) or not code.strip() else
                   "the code is too long" if len(code) > MAX_CODE else
                   "tasks here have no internet" if data.get("network") else
                   f"at most {MAX_SECONDS} seconds" if not 1 <= seconds <= MAX_SECONDS else
                   f"at most {MAX_MEMORY_MB} MB" if not 16 <= memory <= MAX_MEMORY_MB else
                   "bad file name" if not all(isinstance(n, str) and SAFE_NAME.match(n) for n in files) else
                   "too many bytes" if sum(len(v) for v in files.values()) * 3 // 4 > MAX_UPLOAD else "")
        if problem:
            return JSONResponse({"error": problem}, status_code=422)
        task = {"request_id": rid, "kind": kind, "code": code, "files": files, "seconds": seconds,
                "memory_mb": memory, "network": False, "events": [], "state": "pending", "result": None}
        task_id = secrets.token_hex(8)
        receipt = proto.sign_receipt(token, {"request_id": rid, "task_id": task_id, "boot_id": boot_id,
                                             "accepted_at": time.time(), "digest": task_digest(task)})
        with lock:
            if rid in by_request:
                return by_request[rid]
            task.update(task_id=task_id, receipt=receipt)
            event(task, "pending", "accepted")
            tasks[task_id] = task
            by_request[rid] = receipt
            while len(by_request) > KEEP_RECEIPTS:
                by_request.pop(next(iter(by_request)))
            finished = [k for k, t in tasks.items() if t["state"] in ("done", "failed", "cancelled")]
            for k in finished[:max(0, len(tasks) - KEEP_TASKS)]:
                del tasks[k]                                # the oldest finished ones go first
        work.put(task)
        return receipt

    @app.get("/v1/tasks/{task_id}/events")
    async def events(task_id: str, request: Request, after: int = 0):
        if await authorised(request) is None:
            return denied()
        with lock:
            task = tasks.get(task_id)
            if task is None:
                return JSONResponse({"error": "unknown task", "boot_id": boot_id}, status_code=404)
            return {"boot_id": boot_id, "events": [e for e in task["events"] if e["seq"] > after]}

    @app.get("/v1/tasks/{task_id}/result")
    async def result(task_id: str, request: Request):
        if await authorised(request) is None:
            return denied()
        with lock:
            task = tasks.get(task_id)
            if task is None:
                return JSONResponse({"error": "unknown task", "boot_id": boot_id}, status_code=404)
            if task["result"] is None:
                return {"finished": task["state"] == "cancelled", "state": task["state"]}
            return {"finished": True, "state": task["state"], **task["result"]}

    @app.post("/v1/tasks/{task_id}/cancel")
    async def cancel(task_id: str, request: Request):
        if await authorised(request) is None:
            return denied()
        with lock:
            task = tasks.get(task_id)
            if task is None:
                return JSONResponse({"error": "unknown task", "boot_id": boot_id}, status_code=404)
            if task["state"] not in ("done", "failed", "cancelled"):
                event(task, "cancelled", "cancelled by the owner")
            return {"state": task["state"]}

    return app


def app_from_env() -> FastAPI:
    """For Render: the token from the secret file (or the environment), files under /tmp/cloudroom."""
    harden_process()
    secret_file = os.environ.get("CLOUDROOM_TOKEN_FILE", "/etc/secrets/CLOUDROOM_TOKEN")
    token = Path(secret_file).read_text().strip() if Path(secret_file).exists() else os.environ.pop(
        "CLOUDROOM_TOKEN", "")
    return create_app(token, os.environ.get("CLOUDROOM_DIR", "/tmp/cloudroom"),
                      secret_paths=[str(Path(secret_file).parent)])
