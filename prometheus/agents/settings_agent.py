"""Shows or switches which AI models Prometheus uses. Switching waits for the owner's approval."""
from __future__ import annotations

import re

from prometheus.agents.base import Agent, AgentContext, StepResult, register

PROVIDERS = ("openrouter", "anthropic", "ollama")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}")


@register
class SettingsAgent(Agent):
    name = "settings"
    description = ("Shows which AI models Prometheus uses and which are ready, or switches one. Inputs: action = show | "
                   "set_model (provider = openrouter | anthropic | ollama, model = the model name, for example "
                   "deepseek/deepseek-v4.1-flash). Switching waits for the owner's approval.")
    tools = {"settings_read", "settings_write"}
    required_inputs = ("action",)
    accepts_private = False

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        action = str(inputs.get("action") or "show").strip().lower()
        if action == "show":
            ctx.use(self.name, "settings_read")
            ready = {m["name"]: m for m in ctx.router.status_detail()}
            lines = []
            for name, provider in ctx.router.providers.items():
                state = ready.get(name, {})
                if name in PROVIDERS or state.get("available"):
                    status = "resting" if state.get("resting") else ("ready" if state.get("available") else "not set up")
                    lines.append(f"- {name}: {getattr(provider, 'model', '') or '(default)'} ({status})")
            routes = "\n".join(f"- {k}: {', '.join(v)}" for k, v in ctx.router.routes.items())
            return StepResult("done", "Models:\n" + "\n".join(lines) + "\n\nWho does what:\n" + routes)
        if action != "set_model":
            return StepResult("failed", "I can show the models or switch one (action = show or set_model).")
        provider = str(inputs.get("provider") or "").strip().lower()
        model = str(inputs.get("model") or "").strip()
        if provider not in PROVIDERS:
            return StepResult("failed", f"Pick one of: {', '.join(PROVIDERS)}.")
        if not MODEL.fullmatch(model):
            return StepResult("failed", "That doesn't look like a model name (for example deepseek/deepseek-v4.1-flash).")
        ctx.use(self.name, "settings_write")
        ctx.approvals.request("set_model", f"Use {model} for {provider}", {"provider": provider, "model": model})
        return StepResult("waiting_approval", f"Switching {provider} to {model} is waiting for your approval.")
