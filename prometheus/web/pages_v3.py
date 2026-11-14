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
    register_memory_pages(app, services, shell)
    register_system_pages(app, services, shell)
    register_approval_pages(app, services, shell)

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


# ---- Memory ---------------------------------------------------------------------------------------------------------
MEMORY_TABS = ("memories", "files", "answers", "topics", "privacy")
KIND_LABEL = {"memory": "Memory", "chat_note": "Chat note", "agent_note": "Agent note", "message": "Message"}


def register_memory_pages(app: FastAPI, services, shell) -> None:
    import time as _t

    from fastapi import Form

    from prometheus.config import save_settings

    store = services.store

    def facts_view() -> dict:
        facts = services.orchestrator.facts
        people = []
        for subject in facts.subjects():
            rows = [dict(vars(f)) for f in facts.about(subject)]
            people.append({"name": "You" if subject == "me" else subject.title(),
                           "now": [f for f in rows if f["until"] is None],
                           "past": [f for f in rows if f["until"] is not None]})
        return {"people": people, "fact_changes": facts.changes()}

    def off_chats() -> list[int]:
        return list(services.settings.memory_off_chats or [])

    def chat_of_message(ids: list[int]) -> dict[int, int]:
        if not ids:
            return {}
        rows = store._rows(f"SELECT id, chat_id FROM messages WHERE id IN ({','.join('?' * len(ids))})", tuple(ids))
        return {r["id"]: r["chat_id"] for r in rows}

    def generated_files(q: str = "") -> list[dict]:
        rows = store._rows("SELECT o.id, o.chat_id, o.path, o.kind, o.is_dir, o.created, COALESCE(c.title, '') AS chat "
                           "FROM outputs o LEFT JOIN chats c ON c.id = o.chat_id ORDER BY o.id DESC LIMIT 300")
        out = []
        for r in rows:
            name = str(r["path"]).replace("\\", "/").rstrip("/").split("/")[-1]
            if q and q.lower() not in f"{name} {r['chat']}".lower():
                continue
            out.append({**dict(r), "name": name})
        return out

    @app.get("/v3/memory")
    def memory_page(request: Request, tab: str = "memories", q: str = "", show: str = "", said: str = ""):
        tab = tab if tab in MEMORY_TABS else "memories"
        q = q.strip()[:200]
        ctx: dict = {"tab": tab, "q": q, "said": said[:300], "show": show}
        if tab == "memories":
            found = []
            if q:
                hits = store.recall(q, limit=30, kinds=("memory", "chat_note", "agent_note", "message"))
                owner = chat_of_message([h.ref_id for h in hits if h.kind == "message"])
                left_out = set(off_chats())
                found = [{"kind": KIND_LABEL.get(h.kind, h.kind), "text": h.text[:600], "source": h.source,
                          "chat": owner.get(h.ref_id) if h.kind == "message" else None}
                         for h in hits if not (h.kind == "message" and owner.get(h.ref_id) in left_out)]
            ctx.update(memories=list(reversed(store.memories())), found=found, **facts_view())
        elif tab == "files":
            show = show if show in ("all", "pages", "made") else "all"
            ctx["show"] = show
            ctx["passages"] = ([{"title": c.title, "source": c.source, "private": c.private, "text": c.match[:500]}
                                for c in store.search_chunks(q, limit=12, include_private=True, chats=False)]
                               if q and show != "made" else [])
            ctx["documents"] = [d for d in store.documents()
                                if not q or q.lower() in f"{d.title} {d.source}".lower()] if show != "made" else []
            ctx["made"] = generated_files(q) if show in ("all", "made") else []
        elif tab == "answers":
            from prometheus.learning import current_rules

            try:
                text, history = services.prompts.get("learned"), services.prompts.history("learned")[:8]
            except (OSError, RuntimeError, ValueError):
                text, history = "", []
            ctx.update(rules=current_rules(text), history=history, task_lessons=store.lessons(50))
        elif tab == "topics":
            topics = store.topics()
            chats = dict(store.chats())
            for t in topics:
                ids = list(dict.fromkeys(s["chat_id"] for s in store.segments(topic=t["topic"])))
                t["chat_list"] = [(cid, chats.get(cid, "Chat")) for cid in ids if cid in chats][:12]
            ctx.update(topics=topics, folder=str(services.archive.folder) if services.archive else "")
        else:
            everything = store.chats()
            left = set(off_chats())
            ctx.update(paused=bool(services.settings.memory_paused),
                       left_out=[(cid, t) for cid, t in everything if cid in left],
                       chats=[(cid, t) for cid, t in everything if cid not in left][:300],
                       counts={"memories": len(store.memories()), "documents": len(store.documents(limit=100000)),
                               "facts": sum(len(p["now"]) + len(p["past"]) for p in facts_view()["people"])})
        return shell(request, "v3/memory.html", "memory", **ctx)

    def ok(**kw):
        return {"ok": True, **kw}

    def bad(text: str, code: int = 400):
        return JSONResponse({"error": text}, status_code=code)

    @app.post("/api/v3/memory/add")
    def api_memory_add(text: str = Form("")):
        from prometheus.orchestrator.planner import SECRETISH

        text = " ".join(text.split())[:1000]
        if not text:
            return bad("Type what Prometheus should remember.")
        if SECRETISH.search(text):
            return bad("That looks like a password or key: those go in Settings → AI models and keys, not in memory.")
        mid = store.add_memory(text, source="added by you")
        store.audit("user", "add_memory", {"id": mid})
        return ok(id=mid)

    @app.post("/api/v3/memory/{memory_id}/edit")
    def api_memory_edit(memory_id: int, text: str = Form("")):
        from prometheus.orchestrator.planner import SECRETISH

        text = " ".join(text.split())[:1000]
        if not text:
            return bad("A memory can't be empty: delete it instead.")
        if SECRETISH.search(text):
            return bad("That looks like a password or key, so it wasn't saved.")
        if not store.update_memory(memory_id, text):
            return bad("That memory is gone.", 404)
        store.audit("user", "edit_memory", {"id": memory_id})
        return ok()

    @app.post("/api/v3/memory/{memory_id}/delete")
    def api_memory_delete(memory_id: int):
        store.delete_memory(memory_id)
        store.audit("user", "delete_memory", {"id": memory_id})
        return ok()

    @app.post("/api/v3/facts/{fact_id}/edit")
    def api_fact_edit(fact_id: int, text: str = Form("")):
        """A new value for a fact: the old one is kept as past, with its date (like saying 'change X to Y')."""
        value = " ".join(text.split())[:300]
        facts = services.orchestrator.facts
        rows = store._rows("SELECT subject, attribute FROM facts WHERE id = ? AND until IS NULL", (fact_id,))
        if not rows:
            return bad("That fact is gone or already in the past.", 404)
        if not value:
            return bad("Type the new value, or forget it instead.")
        facts.save(rows[0]["subject"], rows[0]["attribute"], value, source="edited by you")
        store.audit("user", "edit_fact", {"id": fact_id})
        return ok()

    @app.post("/api/v3/facts/{fact_id}/forget")
    def api_fact_forget(fact_id: int):
        services.orchestrator.facts.forget(fact_id)
        store.audit("user", "forget_fact", {"id": fact_id})
        return ok()

    @app.post("/api/v3/facts/changes/{change_id}/undo")
    def api_fact_undo(change_id: int):
        if not services.orchestrator.facts.undo_change(change_id):
            return bad("Already undone.", 409)
        store.audit("user", "undo_fact_change", {"id": change_id})
        return ok()

    @app.post("/api/v3/documents/{doc_id}/delete")
    def api_document_delete(doc_id: int):
        store.delete_document(doc_id)
        store.audit("user", "delete_document", {"id": doc_id})
        return ok()

    @app.post("/api/v3/memory/learn")
    def api_memory_learn():
        from prometheus.learning import learn

        try:
            said = learn(services) or "Nothing new to learn yet: rate a few answers first."
        except Exception as e:
            said = f"Couldn't learn right now ({type(e).__name__}). It will try again tonight."
        return ok(message=said)

    @app.post("/api/v3/memory/lessons/reset")
    def api_lessons_reset():
        from prometheus.learning import HEADER

        services.prompts.set("learned", HEADER + "(Reset by the owner.)\n", "Reset by the owner")
        store.audit("user", "lessons_reset", {})
        return ok(message="Cleared. Older versions can still be restored.")

    @app.post("/api/v3/memory/lessons/{commit}/restore")
    def api_lessons_restore(commit: str):
        try:
            services.prompts.rollback("learned", commit)
        except (ValueError, RuntimeError):
            return bad("That version couldn't be restored.", 409)
        store.audit("user", "lessons_restored", {"commit": commit[:12]})
        return ok(message="Restored that version.")

    @app.post("/api/v3/task-lessons/{lesson_id}/delete")
    def api_task_lesson_delete(lesson_id: int):
        store.delete_lesson(lesson_id)
        return ok()

    @app.post("/api/v3/memory/pause")
    def api_memory_pause(on: str = Form("")):
        """on = paused: nothing is saved from chats by itself (what you ask it to remember still is)."""
        services.settings.memory_paused = bool(on)
        save_settings(services.settings)
        store.audit("user", "memory_pause", {"paused": bool(on)})
        return ok(paused=services.settings.memory_paused)

    @app.post("/api/v3/memory/chats/{chat_id}/leave-out")
    def api_memory_leave_out(chat_id: int, on: str = Form("")):
        """on: this chat is never remembered or searched; its searchable parts and its file are removed now."""
        if chat_id not in dict(store.chats()):
            return bad("No such chat.", 404)
        left = [c for c in off_chats() if c != chat_id] + ([chat_id] if on else [])
        services.settings.memory_off_chats = left
        save_settings(services.settings)
        if services.archive is not None:
            try:
                services.archive.sync(chat_id)          # left out: drops its parts; back in: files it again
            except Exception:
                pass
        store.audit("user", "memory_leave_out", {"chat": chat_id, "on": bool(on)})
        return ok(left_out=chat_id in left)

    @app.get("/api/v3/memory/export")
    def api_memory_export():
        """Everything Prometheus remembers about you, as one JSON file (file text itself stays on the laptop)."""
        facts = services.orchestrator.facts
        try:
            rules = services.prompts.get("learned")
        except (OSError, RuntimeError, ValueError):
            rules = ""
        body = {
            "exported": _t.strftime("%Y-%m-%d %H:%M"),
            "memories": [{"text": m.text, "source": m.source, "created": m.created} for m in store.memories()],
            "facts": [dict(vars(f)) for s in facts.subjects() for f in facts.about(s)],
            "how_to_answer": rules,
            "task_lessons": store.lessons(1000),
            "topics": store.topics(1000),
            "documents": [{"title": d.title, "source": d.source, "private": d.private, "chars": d.chars}
                          for d in store.documents(limit=100000)],
        }
        store.audit("user", "memory_export", {})
        return JSONResponse(json.loads(json.dumps(body, default=str)), headers={
            "Content-Disposition": f'attachment; filename="prometheus-memory-{_t.strftime("%Y-%m-%d")}.json"'})

    @app.post("/api/v3/memory/wipe")
    def api_memory_wipe(confirm: str = Form("")):
        if confirm != "forget everything":
            return bad("Type “forget everything” to confirm.")
        store.wipe_memories()
        for d in store.documents(limit=100000):
            store.delete_document(d.id)
        facts = services.orchestrator.facts
        for s in facts.subjects():
            for f in facts.about(s):
                facts.forget(f.id)
        store.audit("user", "wipe_memories", {})
        return ok(message="Forgot everything: memories, facts and saved pages and files. Chats are kept.")


# ---- System (with Activity) -------------------------------------------------------------------------------------------
# Background jobs and the name of the thread each runs in: "Running" is shown only when that thread is really alive.
BACKGROUND = [("Reminders and alarms", "prometheus-reminders", "reminders"),
              ("Watchers (folders, prices, pages)", "prometheus-watchers", "watchers"),
              ("Daily jobs (learning, tidy-up, repos of the day)", "prometheus-daily", "daily"),
              ("Morning briefing", "prometheus-briefing", "briefing"),
              ("Resuming paused tasks", "prometheus-resumer", "resumer"),
              ("Cloud copy sync", "prometheus-sync", "sync"),
              ("Voice", "prometheus-voice", "voice")]
ACTION_WORDS = {"delete": "Deleted", "add": "Added", "edit": "Edited", "forget": "Forgot", "undo": "Undid",
                "switch": "Switched", "remove": "Removed", "retry": "Ran again", "stop": "Stopped", "cancel": "Cancelled",
                "wipe": "Wiped", "export": "Exported", "try": "Tried", "pause": "Paused"}


def plain_action(action: str) -> str:
    """'delete_task' → 'Deleted task', 'skill_switch' → 'Skill switch'."""
    words = action.split("_")
    if words and words[0] in ACTION_WORDS:
        return " ".join([ACTION_WORDS[words[0]]] + words[1:])
    return " ".join(words).capitalize()


def register_system_pages(app: FastAPI, services, shell) -> None:
    import os
    import platform
    import sys
    import threading

    from fastapi import Form

    store = services.store

    def alive(thread_name: str) -> bool:
        return any(t.name == thread_name and t.is_alive() for t in threading.enumerate())

    def overview() -> dict:
        from prometheus.config import data_dir
        from prometheus.launcher import version

        models = []
        for m in services.router.status_detail():
            state = ("Resting" if m["resting"] else "Ready" if m["available"] else "Not set up")
            models.append({**m, "state": state})
        models.sort(key=lambda m: (m["state"] != "Ready", m["state"] != "Resting", m["name"]))   # usable ones first
        jobs = []
        for label, thread, attr in BACKGROUND:
            obj = getattr(services, attr, None)
            state = "Off" if obj is None else ("Running" if alive(thread) else "Set up, not running")
            if attr == "voice" and obj is not None and not alive(thread):
                try:
                    state = "Off" if obj.state() == "off" else state       # voice is off until you switch it on
                except Exception:
                    pass
            jobs.append({"label": label, "state": state})
        me = {}
        try:
            import psutil

            p = psutil.Process(os.getpid())
            me = {"ram_mb": round(p.memory_info().rss / 1048576), "threads": p.num_threads(),
                  "since": p.create_time()}
        except Exception:
            pass
        b = services.budget.state()
        ks = services.killswitch
        return {"models": models, "jobs": jobs, "me": me, "budget": b,
                "stopped": bool(ks.is_set), "stop_reason": getattr(ks, "reason", ""),
                "pending": len(store.big_approvals()), "updated": getattr(services.updater, "updated", "") or "",
                "about": {"version": version(), "python": platform.python_version(),
                          "os": f"{platform.system()} {platform.release()}", "data": str(data_dir()),
                          "machine": platform.machine(), "pid": os.getpid(), "exe": sys.executable}}

    def activity(q: str) -> dict:
        from prometheus import undo

        undo._ready(store)
        log = [dict(r) for r in store._rows("SELECT * FROM undo_log ORDER BY id DESC LIMIT 60")]
        for r in log:
            r["can_undo"] = not r["undone"] and r["kind"] != "email"
        runs = []
        journal = getattr(services, "journal", None)
        if journal is not None:
            for r in journal.runs()[:60]:
                r["summary"] = undo._describe_run(r, journal)
                runs.append(r)
        where, args = "", ()
        if q:
            where, args = "WHERE action LIKE ? OR detail LIKE ?", (f"%{q}%", f"%{q}%")
        audit = []
        for r in store._rows(f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT 200", args):
            try:
                detail = json.loads(r["detail"])
            except ValueError:
                detail = {}
            text = ", ".join(f"{k}: {v}" for k, v in detail.items() if not isinstance(v, (dict, list)))[:200]
            audit.append({"who": "You" if r["actor"] == "user" else r["actor"].replace("_", " ").title(),
                          "what": plain_action(r["action"]), "detail": text, "at": r["created"]})
        return {"log": log, "runs": runs, "audit": audit}

    @app.get("/v3/system")
    def system_page(request: Request, tab: str = "overview", q: str = ""):
        tab = tab if tab in ("overview", "activity") else "overview"
        q = q.strip()[:100]
        ctx = overview() if tab == "overview" else activity(q)
        return shell(request, "v3/system.html", "system", tab=tab, q=q, **ctx)

    @app.post("/api/v3/activity/undo")
    def api_activity_undo(source: str = Form(...), id: str = Form(...)):
        """Undo one thing from the Activity list (file changes, reminders, saved facts, events, setting changes)."""
        from prometheus import undo

        if source == "files":
            journal = getattr(services, "journal", None)
            run = next((r for r in (journal.runs() if journal else []) if r["id"] == id), None)
            if run is None:
                return JSONResponse({"error": "Those file changes aren't in the history any more."}, status_code=404)
            if run.get("active"):
                return JSONResponse({"error": "It's still running: wait until it finishes."}, status_code=409)
            item = {"source": "files", "id": id, "summary": undo._describe_run(run, journal)}
        elif source == "log" and id.isdigit():
            undo._ready(store)
            rows = store._rows("SELECT * FROM undo_log WHERE id = ?", (int(id),))
            if not rows:
                return JSONResponse({"error": "That isn't in the history."}, status_code=404)
            if rows[0]["undone"]:
                return JSONResponse({"error": "That was already undone."}, status_code=409)
            item = {"source": "log", "id": int(id), "summary": rows[0]["summary"]}
        else:
            return JSONResponse({"error": "Unknown item."}, status_code=400)
        try:
            said = undo.perform(services, item)
        except Exception as e:
            return JSONResponse({"error": f"Couldn't undo it: {type(e).__name__}."}, status_code=500)
        store.audit("user", "undo", {"what": item["summary"][:120]})
        return {"ok": True, "message": said}


# ---- Approvals (in your menu) ----------------------------------------------------------------------------------------
def register_approval_pages(app: FastAPI, services, shell) -> None:
    from fastapi import Form

    store = services.store

    @app.get("/v3/approvals")
    def approvals_page(request: Request):
        waiting = store.big_approvals()
        done = [dict(r) for r in store._rows(
            "SELECT id, kind, summary, status, created FROM approvals WHERE status != 'pending' ORDER BY id DESC LIMIT 30")]
        return shell(request, "v3/approvals.html", "approvals", waiting=waiting, done=done)

    @app.post("/api/v3/approvals/{approval_id}")
    def api_approval_decide(approval_id: int, decision: str = Form(...), secret: str = Form("")):
        try:
            item = store.approval(approval_id)
        except KeyError:
            return JSONResponse({"error": "That request is gone."}, status_code=404)
        if decision not in ("approve", "reject"):
            return JSONResponse({"error": "Approve or reject."}, status_code=400)
        if item.status != "pending":
            return JSONResponse({"error": f"Already {item.status}."}, status_code=409)
        said = services.approvals.resolve(approval_id, approved=decision == "approve", secret=secret[:200])
        if said == "Type the password first.":
            return JSONResponse({"error": said}, status_code=400)
        return {"ok": True, "message": said}
