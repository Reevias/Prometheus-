"""Sends a job to Google's Antigravity agent (Gemini API). It runs on Google's computers, not this laptop.

Every job waits for the owner's approval because it costs money, and it never receives personal files.
"""
from __future__ import annotations

import time
from typing import Callable

from prometheus.agents.base import Agent, AgentContext, StepResult, register
from prometheus.config import get_secret

AGENT_ID = "antigravity-preview-09-2026"
MODELS = ("gemini-3.8-flash", "gemini-4-argon", "gemini-3.6-flash", "gemini-3.5-flash-lite")
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_MAX_TOKENS = 200_000
POLL_SECONDS = 5
MAX_WAIT_SECONDS = 30 * 60


def make_client():
    key = get_secret("GEMINI_API_KEY")
    if not key:
        raise RuntimeError("Add a Gemini API key in Settings first.")
    try:
        from google import genai
    except ImportError as e:
        raise RuntimeError('Antigravity needs one extra package. Run:  pip install -e ".[antigravity]"') from e
    return genai.Client(api_key=key)


def run_antigravity_job(payload: dict, client, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Start the job in the background on Google's side and wait for it to finish."""
    interaction = client.interactions.create(
        agent=AGENT_ID,
        input=str(payload["task"]),
        environment="remote",
        background=True,
        agent_config={"type": "antigravity", "model": payload.get("model") or DEFAULT_MODEL,
                      "max_total_tokens": int(payload.get("max_tokens") or DEFAULT_MAX_TOKENS)},
    )
    waited = 0
    while interaction.status == "in_progress" and waited < MAX_WAIT_SECONDS:
        sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        interaction = client.interactions.get(id=interaction.id)
    usage = getattr(interaction, "usage", None)
    out = {"status": interaction.status, "text": getattr(interaction, "output_text", "") or "",
           "tokens": int(getattr(usage, "total_tokens", 0) or 0), "note": ""}
    if interaction.status == "incomplete":
        out["note"] = "It stopped at the token limit set for one job. Ask again to continue, or raise the limit."
    elif interaction.status == "in_progress":
        out["note"] = "It is still running on Google's side after 30 minutes; I stopped waiting."
    elif interaction.status != "completed":
        out["note"] = f"Antigravity finished with status '{interaction.status}'."
    return out


@register
class Antigravity(Agent):
    name = "antigravity"
    description = ("Hands a self-contained job to Google's Antigravity agent, which works in its own computer in the "
                   "cloud (it can write and run code, browse the web and build files there). It cannot see this "
                   "laptop's files. Costs money, so each job waits for the owner's approval. Inputs: task (full "
                   "instructions), model (optional: gemini-3.8-flash or gemini-4-argon).")
    tools = {"cloud_agent"}
    accepts_private = False

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "cloud_agent")
        task = str(inputs.get("task") or goal).strip()
        if not task:
            return StepResult("failed", "No job was given for Antigravity.")
        model = str(inputs.get("model") or DEFAULT_MODEL)
        if model not in MODELS:
            model = DEFAULT_MODEL
        chat_id = (ctx.store.task(ctx.task_id) or {}).get("chat_id")
        ctx.approvals.request(
            "antigravity_job",
            f"Send this job to Google's Antigravity agent ({model})? It runs on Google's computers and usually "
            f"costs $0.25 to $1, sometimes up to $5.",
            {"task": task[:8000], "model": model, "max_tokens": DEFAULT_MAX_TOKENS, "chat_id": chat_id})
        return StepResult("waiting_approval", "The job for Antigravity is waiting for your approval on the Approvals "
                                              "page. The result will appear in this chat when it finishes.")
