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
        base = {"csrf": getattr(request.state, "csrf", ""), "stopped": services.killswitch.is_set,
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
                    doing=current_step(chat_id) if busy else "")

    def chat_page(request: Request, chat_id: int):
        busy = chat_id in services.running
        return page(request, "chat.html", chats=store.chats(), chat_id=chat_id, chat_title=store.chat_title(chat_id),
                    heads=head_choices(services.router), head=services.settings.head_model,
                    messages=store.messages(chat_id),
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
        if have == len(store.messages(chat_id)) and busy == int(chat_id in services.running) and not busy:
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
                    pending=len(store.approvals()),
                    models=services.router.status_detail() if not (mini or core) else [])

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request):
        return page(request, "approvals.html", approvals=store.approvals(), result="")

    @app.post("/approvals/{approval_id}", response_class=HTMLResponse)
    def decide(request: Request, approval_id: int, decision: str = Form(...), secret: str = Form("")):
        secret = secret[:200]
        result = services.approvals.resolve(approval_id, approved=(decision == "approve"), secret=secret)
        return page(request, "approval_list.html", approvals=store.approvals(), result=result)

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

    @app.get("/memory", response_class=HTMLResponse)
    def memory(request: Request, q: str = ""):
        q = q.strip()[:200]
        found = store.recall(q, limit=30, kinds=("memory", "chat_note", "agent_note", "message")) if q else []
        passages = store.search_chunks(q, limit=10, include_private=True) if q else []
        archive = services.archive
        return page(request, "memory.html", memories=store.memories(), q=q, found=found, passages=passages,
                    documents=store.documents(), topics=store.topics(),
                    folder=str(archive.folder) if archive else "")

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
        return page(request, "skills.html", skills=services.skills.catalog(), shown=None, text="",
                    user_folder=str(services.skills.user))

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
        return page(request, "settings.html", status_code, s=s, providers=rows, other_keys=other_keys,
                    heads=head_choices(services.router), head=s.head_model,
                    voice=services.voice.describe() if services.voice is not None else None,
                    vaults=[str(v) for v in __import__("prometheus.agents.obsidian", fromlist=["x"]).detect_vaults()],
                    voices=__import__("prometheus.voice.engines", fromlist=["VOICES"]).VOICES,
                    home=str(services.home), bad_places=bad_places,
                    budget=services.budget.state(), catalog_date=catalog_updated(), **extra)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        return settings_page(request)

    @app.post("/settings", response_class=HTMLResponse)
    def save(request: Request, budget_usd: str = Form(...), ollama_model: str = Form(""),
             anthropic_model: str = Form(""), allow_lan: str = Form(""), openrouter_model: str = Form(""),
             openrouter_fast_model: str = Form(""), github_repo: str = Form("")):
        try:
            budget = float(budget_usd)
            if not 0 <= budget <= 1000:
                raise ValueError
        except ValueError:
            return settings_page(request, 400, error="The spending cap must be a number between 0 and 1000.")
        github_repo = github_repo.strip() or services.settings.github_repo
        if not REPO_NAME.fullmatch(github_repo):
            return settings_page(request, 400, error="The GitHub repository must look like owner/name.")
        new = replace(services.settings, github_repo=github_repo, budget_usd=budget,
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
