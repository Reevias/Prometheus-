"""M2.2: the new-look pages for what Prometheus already does (Tasks first; the others join one by one)."""
from __future__ import annotations

import json
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

# What the owner sees for each stored task status. "new" is a request that hasn't started its first step yet.
STATUS_GROUPS = {
    "running": ("Running", ("new", "running")),
    "paused": ("Paused", ("paused",)),
    "done": ("Done", ("done", "instant", "phone")),
    "failed": ("Failed", ("failed", "interrupted")),
    "stopped": ("Stopped", ("stopped",)),
}
STATUS_LABEL = {"new": "Starting", "running": "Running", "paused": "Paused", "done": "Done", "instant": "Done",
                "phone": "Done", "failed": "Failed", "interrupted": "Interrupted", "stopped": "Stopped"}
PERIODS = {"today": ("Today", 1), "week": ("7 days", 7), "month": ("30 days", 30), "all": ("All time", 0)}


def since_for(period: str, now: float | None = None) -> float:
    days = PERIODS.get(period, PERIODS["all"])[1]
    if not days:
        return 0.0
    now = time.time() if now is None else now
    if days == 1:                                   # "today": from midnight, local time
        t = time.localtime(now)
        return time.mktime((t.tm_year, t.tm_mon, t.tm_mday, 0, 0, 0, 0, 0, -1))
    return now - days * 86400


def took(start: float, end: float) -> str:
    """'4 s', '2 min 5 s', '1 h 3 min'; empty when it never finished."""
    if not start or not end or end < start:
        return ""
    secs = int(end - start)
    if secs < 60:
        return f"{secs} s"
    if secs < 3600:
        return f"{secs // 60} min {secs % 60} s"
    return f"{secs // 3600} h {(secs % 3600) // 60} min"


def register_pages(app: FastAPI, services, shell) -> None:
    store = services.store

    # ---- Tasks ---------------------------------------------------------------------------------------------------
    def task_rows(status: str, q: str, period: str) -> list[dict]:
        statuses = STATUS_GROUPS[status][1] if status in STATUS_GROUPS else ()
        rows = store.list_tasks(statuses, q.strip()[:200], since_for(period))
        for r in rows:
            r["label"] = STATUS_LABEL.get(r["status"], r["status"].title())
            r["live"] = r["status"] in ("new", "running") and r["chat_id"] in services.running
            r["took"] = took(r["created"], r["finished"])
        return rows

    @app.get("/v3/tasks")
    def tasks_page(request: Request, status: str = "all", q: str = "", period: str = "all", view: str = ""):
        status = status if status in STATUS_GROUPS else "all"
        period = period if period in PERIODS else "all"
        counts = store.task_status_counts()
        groups = {k: sum(counts.get(s, 0) for s in v[1]) for k, v in STATUS_GROUPS.items()}
        rows = task_rows(status, q, period)
        ctx = dict(rows=rows, status=status, q=q, period=period, groups=groups, total=sum(counts.values()),
                   STATUS_GROUPS=STATUS_GROUPS, PERIODS=PERIODS, live=any(r["live"] for r in rows))
        if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "task-list":
            return shell(request, "v3/tasks_list.html", "tasks", **ctx)
        return shell(request, "v3/tasks.html", "tasks", **ctx)

    def task_detail(task_id: int) -> dict | None:
        t = store.task(task_id)
        if not t:
            return None
        end = t.get("finished") or 0.0
        steps = []
        for s in store.steps(task_id):
            steps.append({"agent": s.agent.replace("_", " "), "goal": s.goal, "status": s.status,
                          "inputs": s.inputs, "result": s.result, "at": s.created,
                          "data": {k: v for k, v in (s.data or {}).items() if k in ("path", "paths", "url", "model")}})
        outputs = [dict(o) for o in store._rows(
            "SELECT id, path, kind, is_dir, created FROM outputs WHERE chat_id = ? AND created >= ? AND created <= ? "
            "ORDER BY id", (t["chat_id"], t["created"], end or time.time()))]
        return {**t, "label": STATUS_LABEL.get(t["status"], t["status"].title()),
                "live": t["status"] in ("new", "running") and t["chat_id"] in services.running,
                "took": took(t["created"], end), "steps": steps, "notes": store.agent_notes(task_id),
                "outputs": outputs, "chat": store.chat_title(t["chat_id"]) or "Chat",
                "resume_booked": t["status"] == "paused" and _resume_booked(t["chat_id"])}

    def _resume_booked(chat_id: int) -> bool:
        try:
            from prometheus.resume import _queue
            return any(i.get("chat_id") == chat_id for i in _queue(store))
        except Exception:
            return False

    @app.get("/v3/tasks/{task_id}")
    def task_page(request: Request, task_id: int):
        t = task_detail(task_id)
        if t is None:
            return shell(request, "v3/tasks_missing.html", "tasks", status_code=404)
        if request.headers.get("HX-Request") and request.headers.get("HX-Target") == "task-detail":
            return shell(request, "v3/task_detail.html", "tasks", t=t)
        return shell(request, "v3/task.html", "tasks", t=t)

    def _gone():
        return JSONResponse({"error": "That task is gone."}, status_code=404)

    @app.post("/api/v3/tasks/{task_id}/stop")
    def api_task_stop(task_id: int):
        """Stops a running task (its chat's reply), or cancels a paused one so it won't resume by itself."""
        t = store.task(task_id)
        if not t:
            return _gone()
        if t["status"] in ("new", "running") and t["chat_id"] in services.running:
            services.orchestrator.request_stop(t["chat_id"])
            store.audit("user", "stop_task", {"task": task_id})
            return {"stopping": task_id, "message": "Stopping after the current step."}
        if t["status"] == "paused":
            from prometheus import resume
            resume.cancel(store, t["chat_id"])
            store.set_task_status(task_id, "stopped", t.get("answer") or "")
            store.audit("user", "cancel_task", {"task": task_id})
            return {"stopped": task_id, "message": "Cancelled: it won't resume by itself."}
        return JSONResponse({"error": "That task isn't running."}, status_code=409)

    @app.post("/api/v3/tasks/{task_id}/retry")
    def api_task_retry(task_id: int):
        """Runs the same request again, in its chat (a new task; the old one is kept)."""
        t = store.task(task_id)
        if not t:
            return _gone()
        if t["chat_id"] in services.running:
            return JSONResponse({"error": "Prometheus is still answering in that chat. Wait, or stop it."},
                                status_code=409)
        if t["chat_id"] not in dict(store.chats()):
            return JSONResponse({"error": "Its chat was deleted, so it can't run there again."}, status_code=409)
        services.start_task(t["chat_id"], t["text"])
        store.audit("user", "retry_task", {"task": task_id})
        return {"chat_id": t["chat_id"], "url": f"/c/{t['chat_id']}"}

    @app.post("/api/v3/tasks/{task_id}/delete")
    def api_task_delete(task_id: int):
        t = store.task(task_id)
        if not t:
            return _gone()
        if t["status"] in ("new", "running") and t["chat_id"] in services.running:
            return JSONResponse({"error": "Stop it first: it's still running."}, status_code=409)
        if t["status"] == "paused":
            from prometheus import resume
            resume.cancel(store, t["chat_id"])
        store.delete_task(task_id)
        store.audit("user", "delete_task", {"task": task_id, "text": t["text"][:200]})
        return {"deleted": task_id}

    @app.get("/api/v3/tasks/{task_id}/export")
    def api_task_export(task_id: int):
        """A task's record (request, steps, notes, answer) as JSON, for keeping or sharing."""
        t = task_detail(task_id)
        if t is None:
            return _gone()
        body = json.dumps({k: t[k] for k in ("id", "text", "status", "created", "finished", "answer", "steps", "notes",
                                             "outputs", "chat")}, ensure_ascii=False, indent=2, default=str)
        return JSONResponse(json.loads(body), headers={
            "Content-Disposition": f'attachment; filename="prometheus-task-{task_id}.json"'})
