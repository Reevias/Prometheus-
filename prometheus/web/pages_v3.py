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
    register_skill_pages(app, services, shell)

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


# ---- Skills ---------------------------------------------------------------------------------------------------------
# The built-in guides, grouped for the Skills page; skills added from GitHub are "From GitHub", the owner's own "Yours".
# A SKILL.md may name its own group with a "category:" header line.
SKILL_GROUPS = {
    "coding": "Code", "explain-code": "Code", "understand-software": "Code",
    "research": "Research and learning", "learning-roadmap": "Research and learning",
    "file-sorting": "Files and media", "video": "Files and media",
    "memory": "How Prometheus works", "models": "How Prometheus works", "task-planning": "How Prometheus works",
}
GITHUB_LINK = r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?(?:[#?].*)?"


def skill_group(skill) -> str:
    meta = skill.meta or {}
    if meta.get("category"):
        return meta["category"][:40].strip().capitalize()
    if skill.source == "user":
        return "From GitHub" if skill.name.startswith("repo-") else "Yours"
    return SKILL_GROUPS.get(skill.name, "Other")


def register_skill_pages(app: FastAPI, services, shell) -> None:
    import re
    import shutil
    from pathlib import Path

    from fastapi import Form

    from prometheus.config import save_settings
    from prometheus.features import set_skill_on, skill_on
    from prometheus.skillbook import NAME, check

    store = services.store
    book = services.skills

    def uses(name: str, limit: int = 20) -> list[dict]:
        """When the planner or the Guide used this skill: its steps, newest first."""
        rows = store._rows(
            "SELECT s.id, s.task_id, s.goal, s.status, s.created, t.chat_id FROM steps s JOIN tasks t ON t.id = s.task_id "
            "WHERE s.data LIKE ? ORDER BY s.id DESC LIMIT ?", (f'%"skill": "{name}"%', limit))
        return [dict(r) for r in rows]

    def card(s) -> dict:
        meta = s.meta or {}
        return {"name": s.name, "title": s.name.replace("-", " ").capitalize(), "description": s.description,
                "group": skill_group(s), "source": s.source, "on": skill_on(services.settings, s.name),
                "refs": s.references(), "meta": meta,
                "version": meta.get("version", ""), "author": meta.get("author", ""),
                "requires": meta.get("requires") or meta.get("dependencies") or "",
                "permissions": meta.get("permissions") or meta.get("allowed-tools") or "",
                "link": (re.search(r"github\.com/([\w.-]+/[\w.-]+)", s.description or "") or [None, ""])[1].rstrip(".")}

    @app.get("/v3/skills")
    def skills_page(request: Request, q: str = "", said: str = "", tab: str = "guides"):
        from prometheus.agents import tools_agent
        from prometheus.repo_of_day import RepoOfTheDay

        cards = [card(s) for s in book.everything()]
        if q.strip():
            words = q.lower().split()
            cards = [c for c in cards if all(w in f"{c['name']} {c['description']} {c['group']}".lower() for w in words)]
        groups: dict[str, list] = {}
        for c in sorted(cards, key=lambda c: (c["group"] in ("Other",), c["group"], c["name"])):
            groups.setdefault(c["group"], []).append(c)
        counts = {s.name: 0 for s in book.everything()}
        for r in store._rows("SELECT data FROM steps WHERE data LIKE '%\"skill\": %'"):
            try:
                n = json.loads(r["data"]).get("skill")
            except ValueError:
                n = None
            if n in counts:
                counts[n] += 1
        tab = tab if tab in ("guides", "github", "tools") else "guides"
        return shell(request, "v3/skills.html", "skills", groups=groups, q=q, said=said[:500], tab=tab, counts=counts,
                     total=len(counts), off=sum(1 for c in counts if not skill_on(services.settings, c)),
                     user_folder=str(book.user), trending=RepoOfTheDay(services).todays(),
                     own_tools=tools_agent.TOOLS.all() if tools_agent.TOOLS else [])

    @app.get("/v3/skills/{name}")
    def skill_page(request: Request, name: str, ref: str = ""):
        s = book.get_any(name)
        if s is None:
            return shell(request, "v3/skill_missing.html", "skills", status_code=404)
        text, shown = s.body, "SKILL.md"
        if ref and ref != "SKILL.md":
            try:
                text, shown = book.reference(name, ref), ref
            except ValueError:
                pass
        return shell(request, "v3/skill.html", "skills", s=card(s), text=text, shown=shown, uses=uses(name),
                     titles=dict(store.chats()), checks=None)

    def _no_skill():
        return JSONResponse({"error": "No such skill."}, status_code=404)

    @app.post("/api/v3/skills/{name}/switch")
    def api_skill_switch(name: str, on: str = Form("")):
        if book.get_any(name) is None:
            return _no_skill()
        set_skill_on(services.settings, name, bool(on))
        save_settings(services.settings)
        store.audit("user", "skill_switch", {"skill": name, "on": bool(on)})
        return {"skill": name, "on": skill_on(services.settings, name)}

    @app.post("/api/v3/skills/{name}/check")
    def api_skill_check(name: str):
        s = book.get_any(name)
        if s is None:
            return _no_skill()
        results = check(s)
        return {"skill": name, "ok": all(ok for ok, _ in results),
                "results": [{"ok": ok, "text": text} for ok, text in results]}

    @app.post("/api/v3/skills/{name}/try")
    def api_skill_try(name: str, question: str = Form("")):
        """Ask something with this skill, in a new chat (the planner is told which guide to follow)."""
        from prometheus.memory.titles import quick_title

        s = book.get_any(name)
        if s is None:
            return _no_skill()
        if not skill_on(services.settings, name):
            return JSONResponse({"error": "Switch this skill on first."}, status_code=409)
        question = " ".join(question.split())[:1000]
        if not question:
            return JSONResponse({"error": "Type a question to try it with."}, status_code=400)
        chat = store.create_chat(quick_title(question))
        services.start_task(chat, f"Use the {name} skill: {question}")
        store.audit("user", "skill_try", {"skill": name})
        return {"chat_id": chat, "url": f"/c/{chat}"}

    @app.post("/api/v3/skills/{name}/remove")
    def api_skill_remove(name: str):
        """Only skills you added can be removed (built-in ones can be switched off)."""
        s = book.get_any(name)
        if s is None:
            return _no_skill()
        if s.source != "user" or book.user is None or not NAME.match(name):
            return JSONResponse({"error": "Built-in skills can't be removed; switch it off instead."}, status_code=409)
        folder = Path(book.user) / name
        if folder.is_symlink() or not folder.is_dir() or folder.resolve().parent != Path(book.user).resolve():
            return JSONResponse({"error": "That skill's folder isn't where it should be, so it was left alone."},
                                status_code=409)
        shutil.rmtree(folder)
        set_skill_on(services.settings, name, True)          # forget its switch too
        save_settings(services.settings)
        store.audit("user", "skill_remove", {"skill": name})
        return {"removed": name}

    @app.post("/api/v3/skills/add")
    def api_skill_add(link: str = Form("")):
        """Add a skill from a GitHub repository link: reads its README only; nothing is installed or run."""
        from prometheus.repo_of_day import RepoOfTheDay, harmful

        m = re.fullmatch(GITHUB_LINK, link.strip())
        if not m:
            return JSONResponse({"error": "Paste a GitHub repository link, like github.com/owner/name."}, status_code=400)
        full = m.group(1)
        rod = RepoOfTheDay(services)
        info: dict = {}
        try:
            r = rod.http.get(f"https://api.github.com/repos/{full}", headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 404:
                return JSONResponse({"error": f"GitHub has no public repository {full}."}, status_code=404)
            if r.status_code == 200:
                j = r.json()
                full = j.get("full_name") or full
                info = {"description": j.get("description") or "", "language": j.get("language") or "",
                        "stars": j.get("stargazers_count") or 0}
        except Exception:
            info = {}
        if harmful({"full_name": full, **info}):
            return JSONResponse({"error": "That repository looks like it's about tracking people, other people's "
                                          "accounts or attacks, so it wasn't added."}, status_code=409)
        try:
            said = rod.add({"repo": full, **info})
        except Exception as e:
            return JSONResponse({"error": f"Couldn't add {full}: {e}"[:300]}, status_code=502)
        store.audit("user", "skill_add", {"repo": full})
        return {"added": full, "message": said}
