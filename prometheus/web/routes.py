"""Page and htmx routes."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from prometheus.app import Services, apply_settings
from prometheus.auth import verify_password
from prometheus.catalog import catalog_updated, load_catalog
from prometheus.config import KEY_NAMES, delete_secret, save_settings, secrets_set, set_secret

WEB = Path(__file__).resolve().parent


def register_routes(app: FastAPI, services: Services) -> None:
    templates = Jinja2Templates(directory=str(WEB / "templates"))
    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")
    store = services.store

    def page(request: Request, name: str, status_code: int = 200, **ctx):
        base = {"csrf": getattr(request.state, "csrf", ""), "stopped": services.killswitch.is_set,
                "nav": "/" if request.url.path.startswith("/chats") else
                       ("/skills" if request.url.path.startswith("/skills") else request.url.path)}
        return templates.TemplateResponse(request, name, {**base, **ctx}, status_code=status_code)

    def chat_page(request: Request, chat_id: int):
        return page(request, "chat.html", chats=store.chats(), chat_id=chat_id, messages=store.messages(chat_id),
                    notes=store.chat_notes(chat_id), busy=chat_id in services.running)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        chats = store.chats()
        chat_id = chats[0][0] if chats else store.create_chat("First chat")
        return chat_page(request, chat_id)

    @app.post("/chats")
    def new_chat():
        return RedirectResponse(f"/chats/{store.create_chat('New chat')}", status_code=303)

    @app.get("/chats/{chat_id}", response_class=HTMLResponse)
    def show_chat(request: Request, chat_id: int):
        return chat_page(request, chat_id)

    @app.get("/chats/{chat_id}/messages", response_class=HTMLResponse)
    def messages(request: Request, chat_id: int):
        return page(request, "messages.html", messages=store.messages(chat_id), busy=chat_id in services.running)

    @app.post("/chats/{chat_id}/send", response_class=HTMLResponse)
    def send(request: Request, chat_id: int, text: str = Form(...)):
        text = text.strip()[:4000]
        if text and chat_id not in services.running:
            services.start_task(chat_id, text)
        return page(request, "messages.html", messages=store.messages(chat_id), busy=chat_id in services.running)

    @app.post("/chats/{chat_id}/notes", response_class=HTMLResponse)
    def add_note(request: Request, chat_id: int, text: str = Form(...)):
        if text.strip():
            store.add_chat_note(chat_id, text.strip()[:500])
        return page(request, "notes.html", notes=store.chat_notes(chat_id))

    @app.get("/status", response_class=HTMLResponse)
    def status(request: Request, chat: int | None = None, mini: int = 0, core: int = 0):
        task = store.latest_task(chat) if chat else {}
        return page(request, "status.html", mini=bool(mini), core=bool(core), busy=bool(services.running), task=task,
                    steps=store.steps(task["id"]) if task else [], budget=services.budget.state(),
                    pending=len(store.approvals()),
                    models=services.router.status_detail() if not (mini or core) else [])

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request):
        return page(request, "approvals.html", approvals=store.approvals(), result="")

    @app.post("/approvals/{approval_id}", response_class=HTMLResponse)
    def decide(request: Request, approval_id: int, decision: str = Form(...)):
        result = services.approvals.resolve(approval_id, approved=(decision == "approve"))
        return page(request, "approval_list.html", approvals=store.approvals(), result=result)

    @app.get("/memory", response_class=HTMLResponse)
    def memory(request: Request, q: str = ""):
        q = q.strip()[:200]
        found = store.recall(q, limit=30, kinds=("memory", "chat_note", "agent_note", "message")) if q else []
        return page(request, "memory.html", memories=store.memories(), q=q, found=found)

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

        rows = [
            row("ollama", "Ollama", "", kind="local", note="Runs on this laptop. Free and private.", models=[],
                model=s.ollama_model, keyless=True, supported=True, logs=False, signup="", toggle=False, on=True),
            row("openrouter", "OpenRouter", "OPENROUTER_API_KEY", kind="cloud", note="One key, many models.",
                models=[], model=s.openrouter_model, keyless=False, supported=True, logs=False,
                signup="openrouter.ai/keys", toggle=False, on=True),
            row("anthropic", "Claude (Anthropic)", "ANTHROPIC_API_KEY", kind="cloud", note="Paid per use.", models=[],
                model=s.anthropic_model, keyless=False, supported=True, logs=False,
                signup="console.anthropic.com", toggle=False, on=True),
        ]
        for p in load_catalog():
            rows.append(row(p.id, p.name, p.key_name, kind="free", note=p.description, models=list(p.models),
                            model=s.provider_models.get(p.id) or (p.models[0] if p.models else ""),
                            keyless=p.keyless, supported=p.supported, logs=p.logs_prompts,
                            signup=p.signup_url.replace("https://", ""), toggle=p.supported,
                            on=p.id in s.enabled_providers, why=p.why_unsupported))
        # ready first, then usable without a key, then needs a key, then not supported
        rows.sort(key=lambda r: (not r["ready"], not r["supported"], not (r["keyless"] or r["key_set"]),
                                 r["name"].lower()))
        other_keys = [{"name": "GITHUB_TOKEN", "label": KEY_NAMES["GITHUB_TOKEN"], "set": have["GITHUB_TOKEN"]}]
        return page(request, "settings.html", status_code, s=s, providers=rows, other_keys=other_keys,
                    budget=services.budget.state(), catalog_date=catalog_updated(), **extra)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        return settings_page(request)

    @app.post("/settings", response_class=HTMLResponse)
    def save(request: Request, budget_usd: str = Form(...), ollama_model: str = Form(...),
             anthropic_model: str = Form(...), allow_lan: str = Form(""), openrouter_model: str = Form(""),
             openrouter_fast_model: str = Form("")):
        try:
            budget = float(budget_usd)
            if not 0 <= budget <= 1000:
                raise ValueError
        except ValueError:
            return settings_page(request, 400, error="The spending cap must be a number between 0 and 1000.")
        new = replace(services.settings, budget_usd=budget, ollama_model=ollama_model.strip()[:100],
                      anthropic_model=anthropic_model.strip()[:100], allow_lan=bool(allow_lan),
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
        owner = next((p.id for p in load_catalog() if p.key_name == name), None)
        if owner and owner not in services.settings.enabled_providers:   # a saved key switches the provider on
            services.settings.enabled_providers.append(owner)
            save_settings(services.settings)
        apply_settings(services)
        store.audit("user", "key_saved", {"name": name})
        return settings_page(request, key_saved=KEY_NAMES[name])

    @app.post("/settings/providers", response_class=HTMLResponse)
    def set_provider(request: Request, id: str = Form(...), enabled: str = Form(""), model: str = Form("")):
        info = next((p for p in load_catalog() if p.id == id), None)
        if info is None or not info.supported:
            return settings_page(request, 400, key_error="Unknown provider.")
        s = services.settings
        on = [p for p in s.enabled_providers if p != id] + ([id] if enabled else [])
        s.enabled_providers[:] = on
        model = model.strip()[:150]
        if model:
            s.provider_models[id] = model
        save_settings(s)
        apply_settings(services)
        services.router.clear_rest()
        store.audit("user", "provider", {"id": id, "enabled": bool(enabled), "model": model})
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
