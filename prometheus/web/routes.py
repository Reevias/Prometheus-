"""Page and htmx routes."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from prometheus.app import Services
from prometheus.config import save_settings

WEB = Path(__file__).resolve().parent


def register_routes(app: FastAPI, services: Services) -> None:
    templates = Jinja2Templates(directory=str(WEB / "templates"))
    app.mount("/static", StaticFiles(directory=str(WEB / "static")), name="static")
    store = services.store

    def page(request: Request, name: str, status_code: int = 200, **ctx):
        base = {"csrf": getattr(request.state, "csrf", ""), "stopped": services.killswitch.is_set}
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
    def status(request: Request, chat: int | None = None, mini: int = 0):
        task = store.latest_task(chat) if chat else {}
        return page(request, "status.html", mini=bool(mini), task=task,
                    steps=store.steps(task["id"]) if task else [], budget=services.budget.state(),
                    pending=len(store.approvals()), models=services.router.status() if not mini else {})

    @app.get("/approvals", response_class=HTMLResponse)
    def approvals(request: Request):
        return page(request, "approvals.html", approvals=store.approvals(), result="")

    @app.post("/approvals/{approval_id}", response_class=HTMLResponse)
    def decide(request: Request, approval_id: int, decision: str = Form(...)):
        result = services.approvals.resolve(approval_id, approved=(decision == "approve"))
        return page(request, "approval_list.html", approvals=store.approvals(), result=result)

    @app.get("/memory", response_class=HTMLResponse)
    def memory(request: Request):
        return page(request, "memory.html", memories=store.memories())

    @app.post("/memory", response_class=HTMLResponse)
    def add_memory(request: Request, text: str = Form(...)):
        if text.strip():
            store.add_memory(text.strip()[:1000])
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
        return page(request, "settings.html", status_code, s=services.settings, models=services.router.status(),
                    budget=services.budget.state(), **extra)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        return settings_page(request)

    @app.post("/settings", response_class=HTMLResponse)
    def save(request: Request, budget_usd: str = Form(...), ollama_model: str = Form(...),
             anthropic_model: str = Form(...), allow_lan: str = Form("")):
        try:
            budget = float(budget_usd)
            if not 0 <= budget <= 1000:
                raise ValueError
        except ValueError:
            return settings_page(request, 400, error="The spending cap must be a number between 0 and 1000.")
        new = replace(services.settings, budget_usd=budget, ollama_model=ollama_model.strip()[:100],
                      anthropic_model=anthropic_model.strip()[:100], allow_lan=bool(allow_lan))
        save_settings(new)
        services.settings.__dict__.update(new.__dict__)
        services.budget.cap_usd = budget
        store.audit("user", "settings", {"budget_usd": budget, "allow_lan": new.allow_lan})
        return settings_page(request, saved=True)

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
