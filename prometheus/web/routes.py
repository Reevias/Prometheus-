"""Page and htmx routes."""
from __future__ import annotations

import re
import time
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from prometheus.app import Services, apply_settings
from prometheus.auth import verify_password
from prometheus.catalog import catalog_updated, load_catalog
from prometheus.memory.store import MARK_END, MARK_START
from prometheus.models.router import head_choices
from prometheus.memory.titles import PLACEHOLDERS, clean_title, quick_title
from prometheus.web.render import render_message
from prometheus.config import KEY_NAMES, delete_secret, save_settings, secrets_set, set_secret

WEB = Path(__file__).resolve().parent
REPO_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,38}/[A-Za-z0-9._-]{1,100}$")


def clean_note(text: str) -> str:
    return " ".join((text or "").split())[:500]


def register_routes(app: FastAPI, services: Services) -> None:
    templates = Jinja2Templates(directory=str(WEB / "templates"))
    templates.env.filters["md"] = render_message
    templates.env.filters["marked"] = lambda t: Markup(str(escape(t)).replace(MARK_START, "<mark>")
                                                       .replace(MARK_END, "</mark>"))
    templates.env.filters["clock"] = lambda ts: time.strftime("%H:%M", time.localtime(ts)) if ts else ""
    templates.env.filters["day"] = lambda ts: time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")
    store = services.store

    def page(request: Request, name: str, status_code: int = 200, **ctx):
        try:
            pending = len(store.big_approvals())
        except Exception:
            pending = 0
        base = {"csrf": getattr(request.state, "csrf", ""), "stopped": services.killswitch.is_set, "pending": pending,
                "nav": "/" if request.url.path.startswith("/chats") else
                       ("/skills" if request.url.path.startswith("/skills") else request.url.path)}
        return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)

    def current_step(chat_id: int) -> str:
        """What Prometheus is doing right now in this chat, in a few words."""
        task = store.latest_task(chat_id)
        if not task:
            return "Thinking"
        running = [s for s in store.steps(task["id"]) if s.status == "running"]
        return f"{running[-1].agent.replace('_', ' ')}: {running[-1].goal}" if running else "Planning"

    def messages_partial(request: Request, chat_id: int):
        busy = chat_id in services.running
        return page(request, "messages.html", messages=store.messages(chat_id), busy=busy,
                    asks=store.asks_for_chat(chat_id),
                    ratings=store.ratings_for(chat_id),
                    doing=current_step(chat_id) if busy else "")

    def chat_page(request: Request, chat_id: int):
        busy = chat_id in services.running
        return page(request, "chat.html", chats=store.chats(), chat_id=chat_id, chat_title=store.chat_title(chat_id),
                    heads=head_choices(services.router), head=services.settings.head_model,
                    messages=store.messages(chat_id), ratings=store.ratings_for(chat_id),
                    asks=store.asks_for_chat(chat_id),
                    notes=store.chat_notes(chat_id), busy=busy, doing=current_step(chat_id) if busy else "")

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        chats = store.chats()
        chat_id = chats[0][0] if chats else store.create_chat("First chat")
        return chat_page(request, chat_id)

    @app.post("/chats")
    def new_chat():
        return RedirectResponse(f"/chats/{store.create_chat('New chat')}", status_code=303)

    @app.get("/chats/search", response_class=HTMLResponse)
    def search_chats(request: Request, q: str = "", current: int = 0):
        q = q.strip()[:200]
        if not q:
            return page(request, "chat_list.html", chats=store.chats(), chat_id=current)
        return page(request, "chat_search.html", hits=store.search_chats(q), q=q, chat_id=current)

    @app.get("/chats/{chat_id}", response_class=HTMLResponse)
    def show_chat(request: Request, chat_id: int):
        return chat_page(request, chat_id)

    @app.get("/chats/{chat_id}/messages", response_class=HTMLResponse)
    def messages(request: Request, chat_id: int, have: int = -1, busy: int = -1):
        # The page tells us what it already shows; if nothing changed we send nothing, so it doesn't flicker.
        shown = len(store.messages(chat_id)) + len(store.asks_for_chat(chat_id))
        if have == shown and busy == int(chat_id in services.running) and not busy:
            return Response(status_code=204)
        return messages_partial(request, chat_id)

    @app.post("/chats/{chat_id}/send", response_class=HTMLResponse)
    def send(request: Request, chat_id: int, text: str = Form(...)):
        text = text.strip()[:4000]
        if text and chat_id not in services.running:
            title = dict(store.chats()).get(chat_id, "")
            if title in PLACEHOLDERS and not store.messages(chat_id) and store.chat_named_by(chat_id) != "user":
                store.rename_chat(chat_id, quick_title(text))      # the planner may improve it in a moment
            services.start_task(chat_id, text)
        return messages_partial(request, chat_id)

    @app.post("/chats/{chat_id}/upload", response_class=HTMLResponse)
    async def upload(request: Request, chat_id: int):
        from prometheus.phone import save_upload, what_to_do

        form = await request.form()
        f = form.get("file")
        if f is None or not getattr(f, "filename", ""):
            return messages_partial(request, chat_id)
        data = await f.read()
        try:
            path = save_upload(services.home, f.filename, data)
        except ValueError as e:
            store.add_message(chat_id, "assistant", str(e), "phone")
            return messages_partial(request, chat_id)
        if chat_id not in services.running:
            services.start_task(chat_id, what_to_do(path, str(form.get("text") or "")))
        return messages_partial(request, chat_id)

    @app.get("/push/key")
    def push_key():
        from prometheus.phone import vapid_keys

        return HTMLResponse(vapid_keys()[1], media_type="text/plain")

    @app.post("/push/subscribe")
    async def push_subscribe(request: Request):
        import json as _json

        from prometheus.phone import push, subscribe

        try:
            subscribe(store, _json.loads(await request.body()))
        except (ValueError, TypeError):
            return HTMLResponse("That isn't a push subscription.", status_code=400)
        store.audit("user", "phone_notifications", {})
        import threading as _th

        def hello():
            try:
                push(store, "Prometheus", "Notifications are on. You'll get reminders and alerts here.")
            except Exception:
                pass                       # the push service was unreachable: the next one will try again

        _th.Thread(target=hello, daemon=True).start()
        return HTMLResponse("ok")

    @app.post("/chats/{chat_id}/rename")
    def rename(request: Request, chat_id: int, title: str = Form("")):
        # The new name comes from the form, or from htmx's prompt box.
        title = clean_title(title or unquote(request.headers.get("HX-Prompt", "")))
        if title and chat_id in dict(store.chats()):
            store.rename_chat(chat_id, title, by="user")
            if services.archive:
                services.archive.sync(chat_id)          # the chat's file gets the new name
        return Response(status_code=204, headers={"HX-Refresh": "true"})

    @app.post("/chats/{chat_id}/delete")
    def delete_chat(request: Request, chat_id: int):
        if chat_id in services.running:
            return Response("Stop the running task first (STOP ALL), then delete the chat.", status_code=409)
        if chat_id in dict(store.chats()):
            topics = store.delete_chat(chat_id)
            if services.archive:
                services.archive.forget(chat_id, topics)
            store.audit("user", "delete_chat", {"chat_id": chat_id})
        return Response(status_code=204, headers={"HX-Redirect": "/"})

    @app.post("/messages/{message_id}/rate", response_class=HTMLResponse)
    def rate_message(request: Request, message_id: int, rating: str = Form(...)):
        msg = store.message(message_id)
        if msg is None or msg.role != "assistant":
            return Response(status_code=404)
        value = 1 if rating.strip() == "1" else -1
        note = clean_note(unquote(request.headers.get("HX-Prompt", "")))
        store.rate(message_id, value, note)
        store.audit("user", "rate", {"message": message_id, "rating": value})
        if value < 0 and msg.chat_id not in services.running:
            services.start_rewrite(msg.chat_id, message_id, note)
        return page(request, "rate.html", m=msg, r=store.rating(message_id))

    @app.post("/memory/learn", response_class=HTMLResponse)
    def learn_now(request: Request):
        from prometheus.learning import learn

        try:
            done = learn(services) or "Nothing new to learn yet: rate a few answers first."
        except Exception as e:
            done = f"Couldn't learn right now ({type(e).__name__}). It will try again tonight."
        return memory_page(request, lesson=done)

    @app.post("/memory/lessons/{commit}", response_class=HTMLResponse)
    def restore_lessons(request: Request, commit: str):
        try:
            services.prompts.rollback("learned", commit)
        except (ValueError, RuntimeError):
            return memory_page(request, lesson="That version couldn't be restored.")
        store.audit("user", "lessons_restored", {"commit": commit[:12]})
        return memory_page(request, lesson="Restored that version.")

    @app.post("/memory/task-lessons/{lesson_id}/delete", response_class=HTMLResponse)
    def delete_task_lesson(request: Request, lesson_id: int):
        store.delete_lesson(lesson_id)
        return memory_page(request)

    @app.post("/memory/lessons-reset", response_class=HTMLResponse)
    def reset_lessons(request: Request):
        from prometheus.learning import HEADER

        services.prompts.set("learned", HEADER + "(Reset by the owner.)\n", "Reset by the owner")
        return memory_page(request, lesson="Cleared what Prometheus learned. Older versions can still be restored.")

    @app.get("/reader/panel", response_class=HTMLResponse)
    def reader_panel(request: Request):
        from prometheus.voice import reader as _reader

        r = _reader.CURRENT
        return page(request, "reader_panel.html", r=r.status() if r is not None else None)

    @app.post("/reader/{action}", response_class=HTMLResponse)
    def reader_action(request: Request, action: str):
        import threading as _th

        from prometheus.voice import reader as _reader

        r = _reader.CURRENT
        if r is not None:
            act = {"pause": r.pause, "continue": r.resume, "back": lambda: r.move(-1), "skip": lambda: r.move(1),
                   "slower": lambda: r.set_speed(False), "faster": lambda: r.set_speed(True), "stop": r.stop,
                   "explain": lambda: _th.Thread(target=r.explain, daemon=True).start()}.get(action)
            if act is not None:
                act()
        return reader_panel(request)

    @app.get("/browser/control", response_class=HTMLResponse)
    def browser_control(request: Request):
        b = services.browser
        started = b is not None and b._thread is not None and b._thread.is_alive()
        return page(request, "browser_ctl.html", started=started, paused=bool(b and b.paused))

    @app.post("/browser/control", response_class=HTMLResponse)
    def browser_toggle(request: Request):
        if services.browser is not None:
            services.browser.paused = not services.browser.paused
            store.audit("user", "browser_takeover", {"paused": services.browser.paused})
        return browser_control(request)

    @app.get("/reminders/list", response_class=HTMLResponse)
    def reminder_list(request: Request):
        from prometheus.reminders import describe

        items = [dict(r, when=describe(r["due"])) for r in store.reminders()]
        return page(request, "reminders.html", reminders=items)

    @app.post("/reminders/{reminder_id}/cancel", response_class=HTMLResponse)
    def cancel_reminder(request: Request, reminder_id: int):
        store.cancel_reminder(reminder_id)
        return reminder_list(request)

    @app.get("/chats/{chat_id}/outputs", response_class=HTMLResponse)
    def chat_outputs(request: Request, chat_id: int):
        items = []
        for o in store.outputs(chat_id):
            o["name"] = Path(o["path"]).name or o["path"]
            o["exists"] = Path(o["path"]).exists()
            items.append(o)
        return page(request, "outputs.html", outputs=items)

    @app.post("/outputs/{output_id}/{how}")
    def open_an_output(request: Request, output_id: int, how: str):
        from prometheus.agents.opener import open_output
        from prometheus.safety.paths import PathBlocked

        item = store.output(output_id)
        if item is None or how not in ("open", "folder"):
            return Response("Not found.", status_code=404)
        vault = services.settings.obsidian_vault
        if how == "open" and vault and item["path"].lower().endswith(".md"):
            from prometheus.agents import obsidian

            note, folder = Path(item["path"]), Path(vault)
            try:
                note.resolve().relative_to(folder.resolve())
                services.policy.check_read(note)
                obsidian.open_uri(obsidian.Vault(folder.resolve()).uri(note.resolve()))
                return Response(f"Opened {note.stem} in Obsidian.", status_code=200)
            except (ValueError, PathBlocked):
                pass
        try:
            text = open_output(item["path"], how, services.policy)
        except PathBlocked:
            return Response("That place is not allowed any more.", status_code=403)
        except OSError as e:
            return Response(f"Windows couldn't open it: {e.strerror or e}", status_code=500)
        store.audit("user", "open_output", {"path": item["path"], "how": how})
        return Response(text, status_code=200)

    @app.post("/chats/{chat_id}/notes", response_class=HTMLResponse)
    def add_note(request: Request, chat_id: int, text: str = Form(...)):
        if text.strip():
            store.add_chat_note(chat_id, text.strip()[:500])
        return page(request, "notes.html", notes=store.chat_notes(chat_id))

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request, chat: int | None = None, mini: int = 0, core: int = 0):
        task = store.latest_task(chat) if chat else {}
        voice = services.voice.state() if services.voice is not None else "off"
        return page(request, "status.html", mini=bool(mini), core=bool(core), busy=bool(services.running), task=task,
                    voice=voice, updated=services.updater.updated if services.updater is not None else "",
                    steps=store.steps(task["id"]) if task else [], budget=services.budget.state(),
                    pending=len(store.big_approvals()),
                    models=services.router.status_detail() if not (mini or core) else [])

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request):
        return page(request, "approvals.html", approvals=store.big_approvals(), result="")

    @app.post("/approvals/{approval_id}", response_class=HTMLResponse)
    def decide(request: Request, approval_id: int, decision: str = Form(...), secret: str = Form("")):
        secret = secret[:200]
        result = services.approvals.resolve(approval_id, approved=(decision == "approve"), secret=secret)
        return page(request, "approval_list.html", approvals=store.big_approvals(), result=result)

    @app.post("/ask/{approval_id}", response_class=HTMLResponse)
    def answer_ask(request: Request, approval_id: int, decision: str = Form(...)):
        """A small yes/no in the chat. allow = this exact thing from now on, in every chat; always = its whole
        site or folder; deny = no."""
        chat_id, inline = store.approval_chat(approval_id)
        if not inline:
            return Response(status_code=404)
        approved = decision in ("allow", "always")
        result = services.approvals.resolve(approval_id, approved=approved,
                                            remember={"allow": "exact", "always": "broad"}.get(decision, ""))
        text = ("Not done: you said no." if not approved else
                (result if not str(result).startswith("Failed") else f"That didn't work: {result}"))
        store.add_message(chat_id, "assistant", str(text), "approval")
        return messages_partial(request, chat_id)

    @app.post("/settings/allowed/{rule_id}/remove", response_class=HTMLResponse)
    def remove_allowed(request: Request, rule_id: int):
        store.remove_allow_rule(rule_id)
        store.audit("user", "allow_rule_removed", {"id": rule_id})
        return settings_page(request, saved=True)

    @app.post("/approvals/{approval_id}/open")
    def open_approval_link(request: Request, approval_id: int):
        """Open a waiting approval's GitHub link in the browser, to look before deciding."""
        import webbrowser

        from prometheus.agents.opener import SAFE_URL

        try:
            a = store.approval(approval_id)
        except KeyError:
            return Response(status_code=404)
        url = str((a.payload or {}).get("url", ""))
        if not (url.startswith("https://github.com/") and SAFE_URL.match(url)):
            return Response("Only GitHub links open from here.", status_code=400)
        webbrowser.open(url)
        return Response(status_code=204)

    @app.post("/settings/repo-of-day", response_class=HTMLResponse)
    def set_repo_of_day(request: Request, on: str = Form("")):
        services.settings.repo_of_day = bool(on)
        save_settings(services.settings)
        store.audit("user", "repo_of_day", {"on": bool(on)})
        return settings_page(request, saved=True)

    def memory_page(request: Request, lesson: str = ""):
        return memory(request, "", lesson)

    @app.get("/memory", response_class=HTMLResponse)
    def memory(request: Request, q: str = "", lesson: str = ""):
        q = q.strip()[:200]
        found = store.recall(q, limit=30, kinds=("memory", "chat_note", "agent_note", "message")) if q else []
        passages = store.search_chunks(q, limit=10, include_private=True) if q else []
        archive = services.archive
        from prometheus.learning import current_rules

        try:
            lessons_text, lessons_history = services.prompts.get("learned"), services.prompts.history("learned")[:8]
        except (OSError, RuntimeError, ValueError):
            lessons_text, lessons_history = "", []
        return page(request, "memory.html", memories=store.memories(), q=q, found=found, passages=passages,
                    lessons=current_rules(lessons_text), lessons_history=lessons_history, lesson=lesson,
                    task_lessons=store.lessons(30),
                    documents=store.documents(), topics=store.topics(),
                    folder=str(archive.folder) if archive else "", **facts_view())

    def facts_view() -> dict:
        import time as _t

        facts = services.orchestrator.facts
        people = []
        for subject in facts.subjects():
            rows = [dict(vars(f), since=_t.strftime("%d %b %Y", _t.localtime(f.created)),
                         ended=_t.strftime("%d %b %Y", _t.localtime(f.until or 0))) for f in facts.about(subject)]
            rows = [type("F", (), r) for r in rows]
            people.append({"name": "You" if subject == "me" else subject,
                           "now": [f for f in rows if f.until is None],
                           "past": [f for f in rows if f.until is not None]})
        return {"people": people, "fact_changes": facts.changes()}

    @app.post("/memory/facts/{fact_id}/forget", response_class=HTMLResponse)
    def forget_fact(request: Request, fact_id: int):
        services.orchestrator.facts.forget(fact_id)
        store.audit("user", "forget_fact", {"id": fact_id})
        return memory_page(request)

    @app.post("/memory/facts/changes/{change_id}/undo", response_class=HTMLResponse)
    def undo_fact_change(request: Request, change_id: int):
        services.orchestrator.facts.undo_change(change_id)
        store.audit("user", "undo_fact_change", {"id": change_id})
        return memory_page(request)

    @app.get("/memory/topic", response_class=HTMLResponse)
    def memory_topic(request: Request, t: str = ""):
        t = t.strip()[:60]
        parts = store.segments(topic=t) if t else []
        for p in parts:
            first = next((m for m in store.messages(p["chat_id"]) if m.id >= p["first_msg"] and m.role == "user"),
                         None)
            p["asked"] = first.text[:200] if first else ""
        return page(request, "memory_topic.html", topic=t, parts=parts)

    @app.post("/documents/{doc_id}/delete", response_class=HTMLResponse)
    def delete_document(request: Request, doc_id: int):
        store.delete_document(doc_id)
        return page(request, "document_list.html", documents=store.documents())

    @app.post("/memory", response_class=HTMLResponse)
    def add_memory(request: Request, text: str = Form(...)):
        if text.strip():
            store.add_memory(text.strip()[:1000], source="added by you")
        return page(request, "memory_list.html", memories=store.memories())

    @app.post("/memory/{memory_id}/delete", response_class=HTMLResponse)
    def delete_memory(request: Request, memory_id: int):
        store.delete_memory(memory_id)
        return page(request, "memory_list.html", memories=store.memories())

    @app.post("/memory/wipe", response_class=HTMLResponse)
    def wipe(request: Request):
        store.wipe_memories()
        for d in store.documents(limit=100000):
            store.delete_document(d.id)
        store.audit("user", "wipe_memories", {})
        return page(request, "memory_list.html", memories=[])

    @app.get("/skills", response_class=HTMLResponse)
    def skills_page(request: Request):
        from prometheus.agents import tools_agent

        return page(request, "skills.html", skills=services.skills.catalog(), shown=None, text="",
                    user_folder=str(services.skills.user),
                    own_tools=tools_agent.TOOLS.all() if tools_agent.TOOLS else [])

    @app.post("/tools/{name}/remove", response_class=HTMLResponse)
    def remove_tool(request: Request, name: str):
        from prometheus.agents import tools_agent
        from prometheus.own_tools import ToolError

        try:
            tools_agent.TOOLS.remove(name)
        except ToolError:
            pass
        store.audit("user", "remove_tool", {"name": name})
        return skills_page(request)

    @app.get("/skills/{name}/{ref}", response_class=HTMLResponse)
    def skill_reference(request: Request, name: str, ref: str):
        skill = services.skills.get(name)
        if skill is None:
            return HTMLResponse("Not found", status_code=404)
        try:
            text = skill.body if ref == "SKILL.md" else services.skills.reference(name, ref)
        except ValueError:
            return HTMLResponse("Not found", status_code=404)
        return page(request, "skills.html", skills=services.skills.catalog(), shown=f"{name} / {ref}", text=text,
                    user_folder=str(services.skills.user))

    # -- feature pages: study, money, exams and deadlines ---------------------------------------
    def _study_chat() -> int:
        return services.briefing.study_chat()

    @app.get("/study", response_class=HTMLResponse)
    def study_page(request: Request, notice: str = "", error: str = ""):
        import json as _json
        import time as _t

        from prometheus.agents import study_agent

        st = study_agent.STUDY
        decks = st.decks() if st else []
        pdfs = []
        if st is not None and st.folder is not None and st.folder.exists():
            for pth in sorted(st.folder.glob("Quiz *.pdf"), key=lambda x: -x.stat().st_mtime)[:20]:
                rows = store._rows("SELECT id FROM outputs WHERE path = ? ORDER BY id DESC LIMIT 1", (str(pth),))
                pdfs.append({"name": pth.stem, "id": rows[0]["id"] if rows else 0,
                             "when": _t.strftime("%d %b %H:%M", _t.localtime(pth.stat().st_mtime))})
        try:
            last = _json.loads(store.kv_get("study_last") or "null")
        except ValueError:
            last = None
        return page(request, "study.html", decks=decks, due_total=sum(d["due"] or 0 for d in decks), last=last,
                    pdfs=pdfs, cloud=services.settings.study_cloud, notice=notice, error=error)

    @app.post("/study/make", response_class=HTMLResponse)
    def study_make(request: Request, path: str = Form(...), deck: str = Form("")):
        text = f"make flashcards from {path.strip()}" + (f" in the deck {deck.strip()}" if deck.strip() else "")
        services.start_task(_study_chat(), text)
        return study_page(request, notice="Making the cards. They'll show here in a minute (progress is in the Study chat).")

    @app.post("/study/quiz")
    def study_quiz(deck: str = Form("")):
        from prometheus.agents import study_agent

        chat = _study_chat()
        services.store.add_message(chat, "user", f"Quiz me on {deck}")
        services.store.add_message(chat, "assistant", study_agent.STUDY.start_quiz(chat, deck), "instant")
        return RedirectResponse(f"/chats/{chat}", status_code=303)

    @app.post("/study/export", response_class=HTMLResponse)
    def study_export(request: Request, deck: str = Form(...)):
        from prometheus.agents import study_agent

        path = study_agent.STUDY.export_anki(deck, services.home / "Documents" / "Prometheus Study")
        return study_page(request, notice=f"Saved {path.name} in Documents/Prometheus Study. In Anki: File → Import.")

    @app.get("/money", response_class=HTMLResponse)
    def money_page(request: Request, notice: str = "", error: str = ""):
        import time as _t

        from prometheus.agents import money_agent

        m = money_agent.MONEY
        now = _t.time()
        cats, recent, total, today = [], [], 0.0, 0.0
        if m is not None:
            by = m.by_category(m.month_start(now))
            budgets = services.settings.money_budgets or {}
            top = max(list(by.values()) + [b for b in budgets.values() if b] + [1])
            for name, amount in by.items():
                b = budgets.get(name)
                cats.append({"name": name, "amount": amount, "budget": b,
                             "pct": round(100 * amount / (b or top)) if (b or top) else 0,
                             "over": bool(b and amount >= b), "near": bool(b and amount >= 0.8 * b)})
            total = sum(by.values())
            today = sum(r["amount"] for r in m._since(m.day_start(now)))
            recent = [{**dict(r), "when": _t.strftime("%d %b %H:%M", _t.localtime(r["created"]))}
                      for r in store._rows("SELECT * FROM spending ORDER BY created DESC LIMIT 25")]
        for c in cats:
            c["pct"] = min(100, c["pct"])
        return page(request, "money.html", cats=cats, recent=recent, total=total, today=today,
                    month_name=_t.strftime("%B %Y"), notice=notice, error=error)

    @app.post("/money/add", response_class=HTMLResponse)
    def money_add(request: Request, amount: str = Form(...), what: str = Form(...)):
        from prometheus.agents import money_agent

        try:
            value = float(amount.replace(",", "").replace("₹", "").strip())
            if not 0 < value < 10_000_000:
                raise ValueError
        except ValueError:
            return money_page(request, error="The amount should be a number, like 250.")
        money_agent.MONEY.add(value, what.strip()[:40], said=what)
        return money_page(request, notice=f"Added ₹{value:,.0f} on {what.strip()[:40]}.")

    @app.post("/money/scan", response_class=HTMLResponse)
    def money_scan(request: Request):
        from prometheus.agents import money_agent
        from prometheus.agents.email_agent import mailbox

        if not services.settings.email_address:
            return money_page(request, error="Connect your email in Settings first.")
        try:
            added = money_agent.MONEY.scan_email(mailbox(), days=7)
        except Exception as e:
            return money_page(request, error=f"Couldn't read your email ({type(e).__name__}).")
        return money_page(request, notice=f"Found {added} new bank or UPI alert(s).")

    @app.post("/money/excel", response_class=HTMLResponse)
    def money_excel(request: Request):
        from prometheus.agents import money_agent

        path = money_agent.MONEY.write_excel(services.home / "Documents" / "Prometheus Money")
        try:
            if services.laptop is not None:
                import os as _os

                _os.startfile(str(path))           # our own sheet, made just now
        except Exception:
            pass
        return money_page(request, notice=f"Saved {path.name} in Documents/Prometheus Money.")

    @app.get("/plans", response_class=HTMLResponse)
    def plans_page(request: Request, notice: str = "", error: str = ""):
        import time as _t
        from datetime import date as _date

        from prometheus.agents import assignments_agent, exams_agent
        from prometheus.assignments import _when

        tr, ex = assignments_agent.TRACKER, exams_agent.EXAMS
        assigns = [{**a, "when": _when(a["due"]), "soon": a["due"] - _t.time() < 3 * 86400}
                   for a in (tr.upcoming(60) if tr else [])]
        exams = []
        for p in (ex.plans() if ex else []):
            days = [{"label": _date.fromisoformat(d["day"]).strftime("%a %d %b"), "topics": ", ".join(d["topics"]),
                     "today": d["day"] == ex.today().isoformat(), "done": d["done"]} for d in ex.days(p["id"])]
            exams.append({"exam": p["exam"], "date": p["exam_date"], "days": days,
                          "left": (_date.fromisoformat(p["exam_date"]) - ex.today()).days})
        return page(request, "plans.html", assignments=assigns, exams=exams, notice=notice, error=error)

    @app.post("/plans/add", response_class=HTMLResponse)
    def plans_add(request: Request, title: str = Form(...), due: str = Form(...)):
        from prometheus.agents import assignments_agent

        said = assignments_agent.TRACKER.tell(f"{title.strip()[:100]} due {due.strip()[:60]}")
        if said is None:
            return plans_page(request, error="I couldn't tell when it's due. Try 'Friday', '15 Nov' or '20/11 5pm'.")
        return plans_page(request, notice=said)

    @app.post("/plans/done", response_class=HTMLResponse)
    def plans_done(request: Request, id: int = Form(...)):
        from prometheus.agents import assignments_agent

        tr = assignments_agent.TRACKER
        rows = store._rows("SELECT title FROM assignments WHERE id = ?", (id,))
        if rows:
            store._write("UPDATE assignments SET done = 1 WHERE id = ?", (id,))
            for r in store.reminders():
                if rows[0]["title"] in r["text"]:
                    store.cancel_reminder(r["id"])
        return plans_page(request, notice=f"Marked {rows[0]['title']} as done." if rows else "")

    @app.get("/phone", response_class=HTMLResponse)
    def phone_page(request: Request):
        import base64 as _b64
        import io as _io

        s = services.settings
        address = (s.cloud_url or "").rstrip("/")
        if not address and getattr(s, "role", "laptop") == "cloud":
            address = str(request.base_url).rstrip("/")
        qr = ""
        if address:
            import qrcode

            img = qrcode.make(address, box_size=8, border=1)
            buf = _io.BytesIO()
            img.save(buf, format="PNG")
            qr = _b64.b64encode(buf.getvalue()).decode()
        return page(request, "phone.html", address=address, qr=qr,
                    apk_url="https://github.com/Reevias/Prometheus-/releases/latest/download/Prometheus.apk")

    @app.get("/costs", response_class=HTMLResponse)
    def costs(request: Request):
        import time as _t

        from prometheus.safety.budget import month_start

        state = services.budget.state()
        report = store.cost_report(month_start())
        days = store.cost_report(_t.time() - 30 * 86400)["by_day"]
        top = max((d["usd"] for d in days), default=0)
        pct = round(100 * state.spent / state.cap) if state.cap else 0
        return page(request, "costs.html", budget=state, report=report, days=days, top=top, pct=pct,
                    all_time=store.total_cost())

    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request):
        return page(request, "history.html", runs=services.journal.runs(), result="")

    @app.post("/history/{run_id}/undo", response_class=HTMLResponse)
    def undo(request: Request, run_id: str):
        report = services.files.undo_run(run_id)
        store.audit("user", "undo", {"run_id": run_id, "restored": report.restored})
        result = f"Put back {report.restored} files." + (" " + "; ".join(report.problems) if report.problems else "")
        return page(request, "history_list.html", runs=services.journal.runs(), result=result)

    def settings_page(request: Request, status_code: int = 200, **extra):
        have = secrets_set()
        state = {m["name"]: m for m in services.router.status_detail()}
        s = services.settings

        def row(pid, name, key_name, **kw):
            st = state.get(pid, {})
            return {"id": pid, "name": name, "key_name": key_name, "key_set": bool(key_name and have.get(key_name)),
                    "ready": bool(st.get("available")) and not st.get("resting"), "resting": st.get("resting", False),
                    "reason": st.get("reason", ""), **kw}

        live = services.router.providers
        rows = [
            row("anthropic", "Claude (Anthropic)", "ANTHROPIC_API_KEY", kind="paid",
                note="The main brain: plans tasks and writes answers. Paid per use, within your spending cap.",
                models=["claude", "anthropic"], model=s.anthropic_model, keyless=False, supported=True, logs=False,
                signup="https://console.anthropic.com/settings/keys", toggle=False, off=False),
            row("openrouter", "OpenRouter", "OPENROUTER_API_KEY", kind="paid",
                note="One key for hundreds of models, including free ones.", models=["deepseek", "llama", "qwen"],
                model=s.openrouter_model, keyless=False, supported=True, logs=False,
                signup="https://openrouter.ai/keys", toggle=False, off=False),
            row("ollama", "Ollama", "", kind="local", note="Runs on this laptop. Free and private; reads your files.",
                models=["local"], model=s.ollama_model, keyless=True, supported=True, logs=False, signup="",
                toggle=False, off=False),
        ]
        for p in load_catalog():
            provider = live.get(p.id)
            rows.append(row(p.id, p.name, "" if p.keyless else p.key_name, kind="free", note=p.description,
                            models=list(p.models), model=getattr(provider, "model", ""), keyless=p.keyless,
                            supported=p.supported, logs=p.logs_prompts,
                            signup="" if p.keyless else p.signup_url, toggle=p.supported,
                            off=p.id in s.disabled_providers, why=p.why_unsupported))
        # working first, then ones with a key, then free without a key, then the rest; unsupported last
        rows.sort(key=lambda r: (not r["supported"], not r["ready"], not r["key_set"], r["kind"] != "paid",
                                 not r["keyless"], r["name"].lower()))
        other_keys = [{"name": "GITHUB_TOKEN", "label": KEY_NAMES["GITHUB_TOKEN"], "set": have["GITHUB_TOKEN"]}]
        from prometheus.safety.paths import extra_root_problem

        bad_places = {f: extra_root_problem(f, services.home) for f in s.extra_folders}
        bad_places = {f: why for f, why in bad_places.items() if why}
        allowed_rules = store.allow_rules()
        if not s.webhook_token:
            import secrets as _secrets

            s.webhook_token = _secrets.token_urlsafe(24)
            save_settings(s)
        webhook_url = f"http://127.0.0.1:{s.port}/hooks/{s.webhook_token}"
        from prometheus.agents import apps_agent
        from prometheus.connectors.email_box import help_for

        mcp = []
        manager = apps_agent.MANAGER
        for e in (manager.entries() if manager else []):
            live = manager.live.get(e["name"]) if manager else None
            err = manager.errors.get(e["name"], "") if manager else ""
            mcp.append({**e, "ok": live is not None, "status": (f"{len(live.tools)} tools" if live else err)})
        return page(request, "settings.html", status_code, s=s, providers=rows, other_keys=other_keys,
                    allowed_rules=allowed_rules, mcp=mcp, webhook_url=webhook_url, email_help=help_for(s.email_address),
                    email_key_set=bool(have.get("EMAIL_PASSWORD")), calendar_set=bool(have.get("CALENDAR_LINKS")),
                    google_client=bool(have.get("GOOGLE_CLIENT")), google_on=bool(have.get("GOOGLE_TOKEN")),
                    heads=head_choices(services.router), head=s.head_model,
                    voice=services.voice.describe() if services.voice is not None else None,
                    vaults=[str(v) for v in __import__("prometheus.agents.obsidian", fromlist=["x"]).detect_vaults()],
                    voices=__import__("prometheus.voice.engines", fromlist=["VOICES"]).VOICES,
                    eleven_voices=__import__("prometheus.voice.cloud", fromlist=["VOICES"]).VOICES,
                    eleven_key=bool(have.get("ELEVENLABS_API_KEY")),
                    speech_models=[(k, v[2]) for k, v in __import__("prometheus.voice.engines",
                                                                    fromlist=["SPEECH"]).SPEECH.items()],
                    home=str(services.home), bad_places=bad_places,
                    budget=services.budget.state(), catalog_date=catalog_updated(),
                    safety=_safety_results(), mcp_config=_mcp_config(), money_categories=_money_categories(),
                    **_backup_view(),
                    research_brains=list(__import__("prometheus.models.router", fromlist=["x"]).RESEARCH_LABELS.items()),
                    file_index_status=(getattr(services.file_index, "status", "") or store.kv_get("file_index_status")
                                       or "Not read yet."), **extra)

    def _mcp_config() -> str:
        import json as _json
        import sys as _sys

        from prometheus.launcher import pythonw

        exe = pythonw() if _sys.platform == "win32" else _sys.executable
        return _json.dumps({"mcpServers": {"prometheus": {"command": exe, "args": ["-m", "prometheus.cli", "mcp"]}}},
                           indent=2)

    @app.post("/settings/mcp-token", response_class=HTMLResponse)
    def new_mcp_token(request: Request):
        from prometheus.config import set_secret
        from prometheus.mcp_server import TOKEN_KEY

        import secrets as _secrets

        set_secret(TOKEN_KEY, _secrets.token_urlsafe(32))
        store.audit("user", "mcp_token", {})
        return settings_page(request, notice="Made a new key. The setup above didn't change; apps just need restarting.")

    def _safety_results() -> dict | None:
        import json as _json
        import time as _t

        raw = store.kv_get("redteam_last")
        if not raw:
            return None
        data = _json.loads(raw)
        data["when_text"] = _t.strftime("%d %b %Y %H:%M", _t.localtime(data["when"]))
        data["passed"] = sum(1 for r in data["results"] if r["passed"])
        data["running"] = bool(getattr(services, "_redteam_running", False))
        return data

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        return settings_page(request)

    @app.post("/settings", response_class=HTMLResponse)
    def save(request: Request, budget_usd: str = Form(...), ollama_model: str = Form(""),
             anthropic_model: str = Form(""), allow_lan: str = Form(""), openrouter_model: str = Form(""),
             openrouter_fast_model: str = Form(""), github_repo: str = Form(""), step_tries: str = Form(""),
             task_budget_usd: str = Form("")):
        try:
            budget = float(budget_usd)
            if not 0 <= budget <= 1000:
                raise ValueError
        except ValueError:
            return settings_page(request, 400, error="The spending cap must be a number between 0 and 1000.")
        try:
            tries = int(step_tries) if step_tries.strip() else services.settings.step_tries
            task_cap = float(task_budget_usd) if task_budget_usd.strip() else services.settings.task_budget_usd
            if not (1 <= tries <= 6 and 0.01 <= task_cap <= 10):
                raise ValueError
        except ValueError:
            return settings_page(request, 400, error="Tries must be 1 to 6, and the task limit between $0.01 and $10.")
        github_repo = github_repo.strip() or services.settings.github_repo
        if not REPO_NAME.fullmatch(github_repo):
            return settings_page(request, 400, error="The GitHub repository must look like owner/name.")
        new = replace(services.settings, github_repo=github_repo, budget_usd=budget, step_tries=tries,
                      task_budget_usd=task_cap,
                      ollama_model=ollama_model.strip()[:100] or services.settings.ollama_model,
                      anthropic_model=anthropic_model.strip()[:100] or services.settings.anthropic_model, allow_lan=bool(allow_lan),
                      openrouter_model=openrouter_model.strip()[:150] or services.settings.openrouter_model,
                      openrouter_fast_model=openrouter_fast_model.strip()[:150])
        save_settings(new)
        services.settings.__dict__.update(new.__dict__)
        apply_settings(services)
        store.audit("user", "settings", {"budget_usd": budget, "allow_lan": new.allow_lan})
        return settings_page(request, saved=True)

    def _password_ok(password: str) -> bool:
        return bool(services.settings.password_hash) and verify_password(password, services.settings.password_hash)

    def _key_gate(request: Request, password: str):
        """Password check for key changes, with the same guess limit as login. Returns an error response or None."""
        client = "keys:" + (request.client.host if request.client else "unknown")
        if not app.state.limiter.attempt(client):
            app.state.sessions.drop(request.cookies.get("prom_session"))
            store.audit("user", "key_guess_lockout", {"client": client})
            return HTMLResponse("Too many wrong passwords. You have been logged out; wait 5 minutes.", status_code=429)
        if not _password_ok(password):
            return settings_page(request, 403, key_error="Wrong password. Nothing was changed.")
        app.state.limiter.reset(client)
        return None

    @app.post("/settings/keys", response_class=HTMLResponse)
    def save_key(request: Request, name: str = Form(...), value: str = Form(...), password: str = Form("")):
        if name not in KEY_NAMES:
            return settings_page(request, 400, key_error="Unknown key.")
        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        try:
            set_secret(name, value)
        except ValueError as e:
            return settings_page(request, 400, key_error=str(e))
        except OSError:
            return settings_page(request, 500, key_error="The key file is busy. Try again in a moment.")
        services.router.clear_rest()
        info = next((p for p in load_catalog() if p.key_name == name), None)
        if info is not None and info.id in services.settings.disabled_providers:   # adding a key switches it on
            services.settings.disabled_providers.remove(info.id)
            save_settings(services.settings)
        apply_settings(services)
        if name == "ELEVENLABS_API_KEY" and services.voice is not None:
            services.voice.restart()               # listening again, now with the ElevenLabs voice
        store.audit("user", "key_saved", {"name": name})
        return settings_page(request, key_saved=KEY_NAMES[name])

    @app.post("/settings/providers", response_class=HTMLResponse)
    def set_provider(request: Request, id: str = Form(...), enabled: str = Form("")):
        """Switch one AI off (or back on). Adding a key is what switches an AI on in the first place."""
        info = next((p for p in load_catalog() if p.id == id), None)
        if info is None or not info.supported:
            return settings_page(request, 400, key_error="Unknown provider.")
        s = services.settings
        s.disabled_providers = [p for p in s.disabled_providers if p != id] + ([] if enabled else [id])
        save_settings(s)
        apply_settings(services)
        services.router.clear_rest()
        store.audit("user", "provider", {"id": id, "enabled": bool(enabled)})
        return settings_page(request, saved=True)

    @app.post("/settings/places", response_class=HTMLResponse)
    def add_place(request: Request, folder: str = Form(...), password: str = Form("")):
        from prometheus.safety.paths import extra_root_problem

        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        folder = folder.strip().strip('"')[:400]
        problem = extra_root_problem(folder, services.home)
        if problem:
            return settings_page(request, 400, place_error=problem)
        resolved = str(Path(folder).expanduser().resolve())
        s = services.settings
        if resolved not in s.extra_folders:
            s.extra_folders.append(resolved)
            save_settings(s)
            apply_settings(services)
            store.audit("user", "place_added", {"folder": resolved})
        return settings_page(request, saved=True)

    @app.post("/settings/places/remove", response_class=HTMLResponse)
    def remove_place(request: Request, folder: str = Form(...)):
        s = services.settings
        s.extra_folders = [f for f in s.extra_folders if f != folder]
        save_settings(s)
        apply_settings(services)
        store.audit("user", "place_removed", {"folder": folder})
        return settings_page(request, saved=True)

    @app.post("/settings/head")
    def set_head(request: Request, head: str = Form("")):
        head = head.strip()
        if head and head not in {e for e, _ in head_choices(services.router)}:
            return Response("That model isn't set up.", status_code=400)
        services.settings.head_model = head
        save_settings(services.settings)
        apply_settings(services)
        store.audit("user", "head_model", {"head": head or "auto"})
        return Response(status_code=204)

    @app.get("/voice/pill", response_class=HTMLResponse)
    def voice_pill(request: Request):
        info = services.voice.describe() if services.voice is not None else {"state": "off"}
        return page(request, "voice_pill.html", v=info)

    @app.post("/voice/mute", response_class=HTMLResponse)
    def voice_mute(request: Request):
        if services.voice is not None:
            muted = services.voice.toggle_mute()
            store.audit("user", "voice_mute", {"muted": muted})
        return voice_pill(request)

    def _train_view(request: Request):
        v = services.voice
        train = {"running": bool(v and v.trainer.running), "progress": v.trainer.progress if v else "",
                 "profile": v.profile if v else None}
        return page(request, "voice_train.html", train=train)

    @app.get("/voice/train", response_class=HTMLResponse)
    def voice_train_status(request: Request):
        return _train_view(request)

    @app.post("/voice/train", response_class=HTMLResponse)
    def voice_train(request: Request):
        if services.voice is not None:
            services.voice.trainer.start()
            store.audit("user", "voice_train", {})
        return _train_view(request)

    @app.post("/voice/train/forget", response_class=HTMLResponse)
    def voice_train_forget(request: Request):
        if services.voice is not None:
            services.voice.profiles.forget()
            services.voice.apply_profile(None)
            services.voice.trainer.progress = "Training forgotten."
        return _train_view(request)

    @app.post("/settings/voice", response_class=HTMLResponse)
    def set_voice(request: Request, on: str = Form("")):
        s = services.settings
        s.voice_enabled = bool(on)
        save_settings(s)
        if services.voice is not None:
            if s.voice_enabled:
                services.voice.start()
            else:
                services.voice.stop()
        store.audit("user", "voice", {"on": s.voice_enabled})
        return settings_page(request, saved=True)

    @app.post("/settings/voice/setup", response_class=HTMLResponse)
    def voice_setup(request: Request):
        if services.voice is not None:
            services.voice.services.settings.voice_enabled = True
            save_settings(services.settings)
            services.voice.download()
            store.audit("user", "voice_setup", {})
        return settings_page(request, saved=True)

    @app.post("/settings/voice/speech", response_class=HTMLResponse)
    def voice_speech(request: Request, speech: str = Form(...)):
        from prometheus.voice.engines import SPEECH

        if speech not in SPEECH:
            return settings_page(request, 400, voice_error="Unknown speech model.")
        services.settings.voice_speech = speech
        save_settings(services.settings)
        v = services.voice
        if v is not None:
            v.models.speech = speech
            if v.loop is not None:
                v.stop()
            if v.models.missing():
                v.download()                       # fetches the new model, then starts listening again
            elif services.settings.voice_enabled:
                v.start()
        store.audit("user", "voice_speech", {"speech": speech})
        return settings_page(request, saved=True)

    @app.post("/settings/voice/name", response_class=HTMLResponse)
    def voice_name(request: Request, name: str = Form(...)):
        from prometheus.voice.engines import VOICES

        if name not in dict(VOICES):
            return settings_page(request, 400, voice_error="Unknown voice.")
        services.settings.voice_name = name
        save_settings(services.settings)
        if services.voice is not None and services.voice.loop is not None:
            services.voice.stop()                  # restart listening with the new voice
            services.voice.start()
        store.audit("user", "voice_name", {"name": name})
        return settings_page(request, saved=True)

    @app.post("/settings/voice/engine", response_class=HTMLResponse)
    def voice_engine(request: Request, engine: str = Form(...), eleven_voice: str = Form("")):
        from prometheus.voice.cloud import VOICE_ID

        if engine not in ("auto", "elevenlabs", "local"):
            return settings_page(request, 400, voice_error="Unknown voice engine.")
        eleven_voice = eleven_voice.strip()
        if eleven_voice and not VOICE_ID.match(eleven_voice):
            return settings_page(request, 400, voice_error="That doesn't look like an ElevenLabs voice ID.")
        services.settings.voice_engine = engine
        if eleven_voice:
            services.settings.eleven_voice = eleven_voice
        save_settings(services.settings)
        if services.voice is not None:
            services.voice.restart()
        store.audit("user", "voice_engine", {"engine": engine})
        return settings_page(request, saved=True)

    @app.post("/settings/voice/clap", response_class=HTMLResponse)
    def voice_clap(request: Request, clap: str = Form(""), barge_in: str = Form(""), sensitivity: float = Form(1.0)):
        s = services.settings
        s.voice_clap, s.voice_barge_in = bool(clap), bool(barge_in)
        s.clap_sensitivity = max(0.3, min(3.0, float(sensitivity)))
        save_settings(s)
        if services.voice is not None:
            services.voice.restart()
        store.audit("user", "voice_clap", {"clap": s.voice_clap, "barge_in": s.voice_barge_in})
        return settings_page(request, saved=True)

    @app.post("/settings/voice/autostart", response_class=HTMLResponse)
    def voice_autostart(request: Request, on: str = Form("")):
        from prometheus.launcher import set_autostart

        try:
            set_autostart(bool(on))
        except RuntimeError as e:
            return settings_page(request, 400, voice_error=str(e))
        services.settings.voice_autostart = bool(on)
        save_settings(services.settings)
        store.audit("user", "autostart", {"on": bool(on)})
        return settings_page(request, saved=True)

    @app.post("/settings/autoupdate", response_class=HTMLResponse)
    def set_autoupdate(request: Request, on: str = Form("")):
        services.settings.auto_update = bool(on)
        save_settings(services.settings)
        store.audit("user", "auto_update", {"on": bool(on)})
        return settings_page(request, saved=True)

    @app.post("/settings/obsidian", response_class=HTMLResponse)
    def connect_obsidian(request: Request, folder: str = Form(...), password: str = Form("")):
        from prometheus.safety.paths import extra_root_problem

        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        path = Path(folder.strip().strip('"')).expanduser()
        if not path.is_dir():
            return settings_page(request, 400, obsidian_error=f"{folder} isn't a folder.")
        resolved = path.resolve()
        s = services.settings
        inside_home = services.policy.root_for(resolved) is not None
        if not inside_home:
            problem = extra_root_problem(str(resolved), services.home)
            if problem:
                return settings_page(request, 400, obsidian_error=problem)
            if str(resolved) not in s.extra_folders:
                s.extra_folders.append(str(resolved))
        if services.policy.is_blocked(resolved) and inside_home:
            return settings_page(request, 400, obsidian_error="That folder is in a blocked place.")
        s.obsidian_vault = str(resolved)
        save_settings(s)
        apply_settings(services)
        store.audit("user", "obsidian_connected", {"vault": str(resolved)})
        return settings_page(request, saved=True)

    @app.post("/settings/obsidian/disconnect", response_class=HTMLResponse)
    def disconnect_obsidian(request: Request):
        services.settings.obsidian_vault = ""
        save_settings(services.settings)
        store.audit("user", "obsidian_disconnected", {})
        return settings_page(request, saved=True)

    @app.post("/settings/email", response_class=HTMLResponse)
    def save_email(request: Request, address: str = Form(...), app_password: str = Form(""), password: str = Form("")):
        from prometheus.connectors.email_box import ADDRESS

        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        address = address.strip()
        if not ADDRESS.fullmatch(address):
            return settings_page(request, 400, conn_error="That isn't an email address.")
        if app_password.strip():
            try:
                set_secret("EMAIL_PASSWORD", app_password)
            except ValueError as e:
                return settings_page(request, 400, conn_error=str(e))
        services.settings.email_address, services.settings.email_via = address, ""
        save_settings(services.settings)
        store.audit("user", "email_connected", {"domain": address.rsplit("@", 1)[-1]})
        from prometheus.agents.email_agent import mailbox
        from prometheus.connectors.email_box import EmailError

        try:                                   # try it now, so a wrong password shows up here, not later
            mailbox().search(unread=True, days=3, limit=1)
        except EmailError as e:
            return settings_page(request, conn_error=f"Saved, but the test didn't work: {e}")
        except Exception as e:
            return settings_page(request, conn_error=f"Saved, but I couldn't reach your email ({type(e).__name__}).")
        return settings_page(request, notice=f"Email connected ✓ ({address}). Try asking \"any new mail?\"")

    @app.get("/settings/email/help", response_class=HTMLResponse)
    def email_help(address: str = ""):
        from html import escape

        from prometheus.connectors.email_box import ADDRESS, help_for, hosts_for

        address = address.strip()
        if not ADDRESS.fullmatch(address):
            return HTMLResponse("")
        domain = address.rsplit("@", 1)[-1].lower()
        if domain in ("gmail.com", "googlemail.com"):
            return HTMLResponse("— it's Gmail: <a href=\"#google-signin\">Sign in with Google</a> is easier (no app password).")
        imap, _ = hosts_for(address)
        return HTMLResponse(escape(f"— make one at {help_for(address)}. Server: {imap}."))

    @app.post("/settings/email/google", response_class=HTMLResponse)
    def email_google(request: Request):
        from prometheus.config import get_secret
        from prometheus.connectors.email_box import EmailError
        from prometheus.connectors.gmail import GmailBox

        if not get_secret("GOOGLE_TOKEN"):
            return settings_page(request, 400, conn_error="Sign in with Google first (below).")
        try:
            address = GmailBox().address
        except EmailError as e:
            return settings_page(request, 400, conn_error=str(e))
        except Exception as e:
            return settings_page(request, 400, conn_error=f"Couldn't reach Gmail ({type(e).__name__}).")
        services.settings.email_address, services.settings.email_via = address, "google"
        save_settings(services.settings)
        store.audit("user", "email_connected", {"via": "google"})
        return settings_page(request, notice=f"Gmail connected ✓ ({address}). Try asking \"any new mail?\"")

    @app.post("/settings/email/disconnect", response_class=HTMLResponse)
    def email_disconnect(request: Request):
        services.settings.email_address, services.settings.email_via = "", ""
        save_settings(services.settings)
        store.audit("user", "email_disconnected", {})
        return settings_page(request, notice="Email disconnected.")

    @app.post("/settings/email/cloud", response_class=HTMLResponse)
    def email_cloud(request: Request, on: str = Form("")):
        services.settings.email_cloud_ok = bool(on)
        save_settings(services.settings)
        apply_settings(services)
        store.audit("user", "email_cloud", {"on": bool(on)})
        return settings_page(request, saved=True)

    @app.post("/settings/calendar", response_class=HTMLResponse)
    def save_calendar(request: Request, links: str = Form(...), password: str = Form("")):
        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        urls = [u.strip() for u in links.replace(",", "\n").split() if u.strip()]
        if not urls or not all(u.startswith(("https://", "webcal://")) for u in urls):
            return settings_page(request, 400, conn_error="Calendar links start with https:// (or webcal://).")
        set_secret("CALENDAR_LINKS", "|".join(urls[:5]))
        store.audit("user", "calendar_connected", {"links": len(urls[:5])})
        return settings_page(request, saved=True)

    @app.post("/settings/apps", response_class=HTMLResponse)
    def add_app(request: Request, name: str = Form(...), target: str = Form(...), secret: str = Form(""),
                password: str = Form("")):
        from prometheus.connectors.manager import NAME, secret_name

        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        name, target = name.strip(), target.strip()[:500]
        if not NAME.fullmatch(name):
            return settings_page(request, 400, conn_error="Use a short name with letters, numbers, spaces or -.")
        kind = "url" if target.startswith(("https://", "http://")) else "command"
        s = services.settings
        s.mcp_servers = [e for e in s.mcp_servers if e.get("name", "").lower() != name.lower()]
        s.mcp_servers.append({"name": name, "kind": kind, "target": target, "enabled": True})
        if secret.strip():
            try:
                set_secret(secret_name(name), secret.strip())
            except ValueError as e:
                return settings_page(request, 400, conn_error=str(e))
        save_settings(s)
        from prometheus.agents import apps_agent

        apps_agent.MANAGER.drop(name)
        try:
            apps_agent.MANAGER.get(name)                 # start it now, so mistakes show at once
        except Exception:
            pass
        store.audit("user", "app_connected", {"name": name, "kind": kind})
        return settings_page(request, saved=True)

    @app.post("/settings/apps/{name}/toggle", response_class=HTMLResponse)
    def toggle_app(request: Request, name: str):
        from prometheus.agents import apps_agent

        for e in services.settings.mcp_servers:
            if e.get("name") == name:
                e["enabled"] = not e.get("enabled")
                if not e["enabled"]:
                    apps_agent.MANAGER.drop(name)
        save_settings(services.settings)
        return settings_page(request, saved=True)

    @app.post("/settings/apps/{name}/remove", response_class=HTMLResponse)
    def remove_app(request: Request, name: str):
        from prometheus.agents import apps_agent
        from prometheus.connectors.manager import secret_name

        services.settings.mcp_servers = [e for e in services.settings.mcp_servers if e.get("name") != name]
        save_settings(services.settings)
        apps_agent.MANAGER.drop(name)
        try:
            delete_secret(secret_name(name))
        except OSError:
            pass
        store.audit("user", "app_removed", {"name": name})
        return settings_page(request, saved=True)

    def _money_categories() -> list[str]:
        from prometheus.money import CATEGORIES

        return [c for c, _ in CATEGORIES] + ["Transfers", "Other"]

    @app.post("/settings/assignments", response_class=HTMLResponse)
    def save_assignments(request: Request, senders: str = Form("")):
        services.settings.assignment_senders = [x.strip()[:80] for x in senders.split(",") if x.strip()][:20]
        save_settings(services.settings)
        store.audit("user", "assignment_settings", {"senders": len(services.settings.assignment_senders)})
        return settings_page(request, saved=True)

    @app.post("/settings/focus", response_class=HTMLResponse)
    def save_focus(request: Request, dnd: str = Form(""), apps: str = Form(""), pomodoro: str = Form(""),
                   work: str = Form("25"), rest: str = Form("5")):
        s = services.settings
        try:
            s.focus_work, s.focus_break = max(5, min(180, int(work))), max(1, min(60, int(rest)))
        except ValueError:
            return settings_page(request, 400, error="Work and break are minutes, like 25 and 5.")
        s.focus_dnd, s.focus_pomodoro = bool(dnd), bool(pomodoro)
        s.focus_apps = [a.strip()[:40] for a in apps.split(",") if a.strip()][:20]
        save_settings(s)
        store.audit("user", "focus_settings", {"apps": len(s.focus_apps)})
        return settings_page(request, saved=True)

    @app.post("/settings/clips", response_class=HTMLResponse)
    def save_clips(request: Request, on: str = Form("")):
        services.settings.clip_on = bool(on)
        save_settings(services.settings)
        store.audit("user", "clip_settings", {"on": bool(on)})
        return settings_page(request, saved=True)

    @app.post("/settings/clips/forget", response_class=HTMLResponse)
    def forget_clips(request: Request):
        if services.clips is not None:
            store._write("DELETE FROM clips")
        store.audit("user", "clips_forget", {})
        return settings_page(request, notice="Forgot everything copied so far.")

    @app.post("/settings/money", response_class=HTMLResponse)
    async def save_money(request: Request):
        form = await request.form()
        budgets = {}
        for i, cat in enumerate(_money_categories()):
            raw = str(form.get(f"budget_{i}", "")).replace(",", "").replace("₹", "").strip()
            if raw:
                try:
                    budgets[cat] = max(0.0, float(raw))
                except ValueError:
                    return settings_page(request, 400, error=f"The {cat} budget should be a number.")
        services.settings.money_on = bool(form.get("on"))
        services.settings.money_budgets = budgets
        save_settings(services.settings)
        store.audit("user", "money_settings", {"budgets": len(budgets)})
        return settings_page(request, saved=True)

    def _backup_view() -> dict:
        import json as _json
        import time as _t

        from prometheus.config import get_secret

        try:
            last = _json.loads(store.kv_get("backup_last") or "null")
        except ValueError:
            last = None
        if last:
            last = {**last, "when_text": _t.strftime("%d %b %Y %H:%M", _t.localtime(last["when"])),
                    "mb": f"{last['bytes'] / 1024 ** 2:.1f}"}
        return {"sync_status": store.kv_get("sync_status") or "", "sync_key_set": bool(get_secret("SYNC_TOKEN")),
                "backup_ready": bool(get_secret("BACKUP_KEY")), "backup_last": last,
                "backup_error": store.kv_get("backup_error") or ""}

    @app.post("/settings/phone/pair", response_class=HTMLResponse)
    def phone_pair(request: Request, target: str = Form(""), code: str = Form("")):
        from prometheus.connectors.android import Phone, PhoneError

        try:
            said = Phone().pair(target.strip(), code.strip())
        except PhoneError as e:
            return settings_page(request, 400, phone_error=str(e))
        return settings_page(request, notice=said + " Now connect (step 2).")

    @app.post("/settings/phone/connect", response_class=HTMLResponse)
    def phone_connect(request: Request, target: str = Form("")):
        from prometheus.connectors.android import Phone, PhoneError

        try:
            said = Phone().connect(target.strip())
        except PhoneError as e:
            return settings_page(request, 400, phone_error=str(e))
        services.settings.phone_target = target.strip()
        save_settings(services.settings)
        store.audit("user", "phone_connected", {})
        return settings_page(request, notice=said)

    @app.post("/settings/cloud", response_class=HTMLResponse)
    def save_cloud(request: Request, url: str = Form(""), key: str = Form(""), password: str = Form("")):
        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        url = url.strip().rstrip("/")
        if url and not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?", url):
            return settings_page(request, 400, error="The cloud address looks like https://name.your-tailnet.ts.net")
        if key.strip():
            try:
                set_secret("SYNC_TOKEN", key.strip())
            except ValueError as e:
                return settings_page(request, 400, error=str(e))
        services.settings.cloud_url = url
        save_settings(services.settings)
        store.audit("user", "cloud_copy", {"set": bool(url)})
        if url and services.sync is not None:
            return settings_page(request, notice=services.sync.once() or "Saved.")
        return settings_page(request, saved=True)

    @app.post("/settings/cloud/sync", response_class=HTMLResponse)
    def cloud_sync_now(request: Request):
        said = services.sync.once() if services.sync is not None else ""
        return settings_page(request, notice=said or "Add the cloud address and sync key first.")

    @app.post("/settings/backup/setup", response_class=HTMLResponse)
    def backup_setup(request: Request):
        from prometheus import backup
        from prometheus.config import get_secret

        if get_secret("BACKUP_KEY"):
            return settings_page(request, 400, error="Backups are already set up.")
        code = backup.set_up()
        services.settings.backup_on = True
        save_settings(services.settings)
        store.audit("user", "backup_setup", {})
        return settings_page(request, backup_code=code)

    @app.post("/settings/backup", response_class=HTMLResponse)
    def backup_toggle(request: Request, on: str = Form("")):
        services.settings.backup_on = bool(on)
        save_settings(services.settings)
        return settings_page(request, saved=True)

    @app.post("/settings/backup/now", response_class=HTMLResponse)
    def backup_now(request: Request):
        import threading as _th

        from prometheus import backup

        def work():
            try:
                store.kv_set("backup_error", "")
                backup.run(services)
            except Exception as e:
                store.kv_set("backup_error", f"{type(e).__name__}: {str(e)[:200]}")

        _th.Thread(target=work, name="prometheus-backup", daemon=True).start()
        return settings_page(request, notice="Backing up in the background. Refresh in a minute to see it.")

    @app.post("/settings/research-brain", response_class=HTMLResponse)
    def research_brain(request: Request, brain: str = Form(...)):
        from prometheus.models.router import RESEARCH_BRAINS

        if brain not in RESEARCH_BRAINS:
            return settings_page(request, 400, error="Unknown research brain.")
        services.settings.research_brain = brain
        save_settings(services.settings)
        apply_settings(services)
        store.audit("user", "research_brain", {"brain": brain})
        return settings_page(request, saved=True)

    @app.post("/settings/study", response_class=HTMLResponse)
    def save_study(request: Request, cloud: str = Form(""), reminder: str = Form("")):
        reminder = reminder.strip()
        if reminder and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", reminder):
            return settings_page(request, 400, error="Times look like 18:00.")
        services.settings.study_cloud, services.settings.study_reminder = bool(cloud), reminder
        save_settings(services.settings)
        store.audit("user", "study_settings", {"cloud": bool(cloud)})
        return settings_page(request, saved=True)

    @app.post("/settings/notifications", response_class=HTMLResponse)
    async def save_notifications(request: Request):
        form = await request.form()
        on = set(form.getlist("on"))
        s = services.settings
        for name in ("notify_tasks", "notify_waiting", "notify_briefing", "notify_repo", "notify_updates", "tray"):
            setattr(s, name, name in on)
        save_settings(s)
        store.audit("user", "notifications", {"on": sorted(on)})
        return settings_page(request, saved=True)

    @app.post("/settings/safety-check", response_class=HTMLResponse)
    def safety_check(request: Request):
        import threading as _th

        from prometheus import redteam

        if not getattr(services, "_redteam_running", False):
            def work():
                services._redteam_running = True
                try:
                    redteam.run_all(services.orchestrator, store)
                finally:
                    services._redteam_running = False

            _th.Thread(target=work, name="prometheus-safety-check", daemon=True).start()
        store.audit("user", "safety_check", {})
        return settings_page(request, notice="Safety check started. Refresh this page in a minute for the results.")

    @app.post("/settings/read-files", response_class=HTMLResponse)
    def read_files_now(request: Request):
        if services.file_index is not None:
            services.file_index.read_now()
            store.audit("user", "read_files_now", {})
        return settings_page(request, notice="Reading your files in the background. Searching works as it goes.")

    @app.post("/settings/briefing", response_class=HTMLResponse)
    def save_briefing(request: Request, morning: str = Form("08:00"), evening: str = Form("21:00"),
                      morning_on: str = Form(""), evening_on: str = Form(""), place: str = Form(""),
                      senders: str = Form("")):
        times = [t.strip() for t in (morning, evening)]
        if not all(re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t) for t in times):
            return settings_page(request, 400, error="Times look like 08:00 or 21:30.")
        s = services.settings
        s.briefing_morning, s.briefing_evening = times
        s.briefing_morning_on, s.briefing_evening_on = bool(morning_on), bool(evening_on)
        s.weather_place = " ".join(place.split())[:80]
        s.email_alert_senders = [x.strip()[:80] for x in senders.split(",") if x.strip()][:30]
        save_settings(s)
        store.audit("user", "briefing", {"morning": s.briefing_morning, "evening": s.briefing_evening})
        return settings_page(request, saved=True)

    @app.post("/settings/webhook/new", response_class=HTMLResponse)
    def new_webhook(request: Request):
        import secrets as _secrets

        services.settings.webhook_token = _secrets.token_urlsafe(24)
        save_settings(services.settings)
        store.audit("user", "webhook_new", {})
        return settings_page(request, saved=True)

    @app.post("/settings/watch", response_class=HTMLResponse)
    def set_watch(request: Request, key: str = Form(...), on: str = Form("")):
        if key not in ("watch_battery", "watch_disk", "watch_cpu"):
            return Response(status_code=400)
        setattr(services.settings, key, bool(on))
        save_settings(services.settings)
        store.audit("user", "watcher", {"key": key, "on": bool(on)})
        return settings_page(request, saved=True)

    @app.post("/settings/watch/folder", response_class=HTMLResponse)
    def watch_folder(request: Request, path: str = Form(...), goal: str = Form("")):
        from prometheus.agents.files import resolve_place
        from prometheus.safety.paths import PathBlocked

        folder = resolve_place(path.strip()[:400], services.home)
        try:
            folder = services.policy.check_read(folder)
        except PathBlocked as e:
            return settings_page(request, 400, error=str(e))
        if not folder.is_dir():
            return settings_page(request, 400, error=f"{folder} isn't a folder.")
        s = services.settings
        s.watch_folders = [w for w in s.watch_folders if w.get("path") != str(folder)]
        s.watch_folders.append({"path": str(folder), "goal": " ".join(goal.split())[:300]})
        save_settings(s)
        store.audit("user", "watch_folder", {"path": str(folder)})
        return settings_page(request, saved=True)

    @app.post("/settings/watch/folder/remove", response_class=HTMLResponse)
    def unwatch_folder(request: Request, path: str = Form(...)):
        services.settings.watch_folders = [w for w in services.settings.watch_folders if w.get("path") != path]
        save_settings(services.settings)
        return settings_page(request, saved=True)

    google_pending: dict = {}

    @app.post("/settings/google", response_class=HTMLResponse)
    def save_google(request: Request, client_id: str = Form(...), client_secret: str = Form(...), password: str = Form("")):
        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        cid, csecret = client_id.strip(), client_secret.strip()
        if not re.fullmatch(r"[\w.-]+\.apps\.googleusercontent\.com", cid) or not re.fullmatch(r"[\w-]{10,100}", csecret):
            return settings_page(request, 400, conn_error="That doesn't look like a Google Desktop client ID and secret.")
        set_secret("GOOGLE_CLIENT", f"{cid}|{csecret}")
        store.audit("user", "google_client", {})
        return settings_page(request, saved=True)

    @app.post("/google/start")
    def google_start(request: Request):
        import webbrowser

        from prometheus.connectors.google import GoogleError, start_sign_in

        host = request.url.hostname if request.url.hostname in ("127.0.0.1", "localhost") else "127.0.0.1"
        try:
            url, pending = start_sign_in(f"http://{host}:{request.url.port or services.settings.port}/google/callback")
        except GoogleError as e:
            return Response(str(e), status_code=400)
        google_pending.clear()
        google_pending.update(pending)
        webbrowser.open(url)
        return Response("Google sign-in opened in your browser.", status_code=200)

    @app.get("/google/callback", response_class=HTMLResponse)
    def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
        import hmac as _hmac

        from prometheus.connectors.google import GoogleError, finish_sign_in

        expected = google_pending.get("state", "")
        if error or not code or not expected or not _hmac.compare_digest(state, expected) \
                or time.time() - google_pending.get("made", 0) > 600:
            return HTMLResponse("<p>That Google sign-in didn't work. Try again from Prometheus Settings.</p>", 400)
        try:
            finish_sign_in(code, dict(google_pending))
        except GoogleError as e:
            return HTMLResponse(f"<p>{e}</p>", 400)
        google_pending.clear()
        store.audit("user", "google_connected", {})
        return HTMLResponse("<p>Google is connected. You can close this tab and go back to Prometheus.</p>")

    @app.post("/settings/free", response_class=HTMLResponse)
    def set_free(request: Request, on: str = Form("")):
        s = services.settings
        s.free_no_key = bool(on)
        save_settings(s)
        apply_settings(services)
        store.audit("user", "free_no_key", {"on": s.free_no_key})
        return settings_page(request, saved=True)

    @app.post("/settings/keys/delete", response_class=HTMLResponse)
    def remove_key(request: Request, name: str = Form(...), password: str = Form("")):
        if name not in KEY_NAMES:
            return settings_page(request, 400, key_error="Unknown key.")
        denied = _key_gate(request, password)
        if denied is not None:
            return denied
        try:
            delete_secret(name)
        except OSError:
            return settings_page(request, 500, key_error="The key file is busy. Try again in a moment.")
        services.router.clear_rest()
        apply_settings(services)
        store.audit("user", "key_removed", {"name": name})
        return settings_page(request, key_saved="")

    @app.post("/kill")
    def kill():
        services.killswitch.trigger("Stopped by the user")
        store.audit("user", "kill", {})
        return HTMLResponse("")

    @app.post("/kill/reset")
    def kill_reset():
        services.killswitch.reset()
        store.audit("user", "kill_reset", {})
        return HTMLResponse("")
