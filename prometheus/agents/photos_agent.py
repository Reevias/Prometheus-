"""Finds photos by what they show or the text in them, and sorts them into folders (asked first)."""
from __future__ import annotations

from prometheus import photos
from prometheus.agents.base import Agent, AgentContext, StepResult, register

SHOWN = 20


@register
class PhotosAgent(Agent):
    name = "photos"
    description = ("The owner's photos, looked at on this laptop: find (query = what the photo shows, like beach, "
                   "food, selfie, documents, screenshots, receipts, or words written in it like 'Aadhaar'), sort "
                   "(moves photos into folders like Pictures/Beach and Pictures/Documents, asked first, undoable), "
                   "status (how many are read).")
    tools = {"read_files", "move_files"}
    private_outputs = True
    required_inputs = ("action",)

    def run(self, goal: str, inputs: dict, ctx: AgentContext) -> StepResult:
        ctx.use(self.name, "read_files")
        index = photos.CURRENT
        if index is None:
            return StepResult("failed", "Photo reading isn't set up.")
        action = str(inputs.get("action") or "").strip().lower()
        status = ctx.store.kv_get(index.KV) or "No photos read yet; they're read at night"
        if action == "status":
            return StepResult("done", status + ".")
        if action == "find":
            query = str(inputs.get("query") or goal)
            found = [p for p in index.find(query) if not ctx.policy.is_blocked(p)]
            if not found:
                return StepResult("done", f"I found no photos of that. ({status}.)", {"paths": []})
            for p in found[:SHOWN]:
                ctx.store.add_output(ctx.task_id, p, "found")
            return StepResult("done", f"{len(found)} photo(s):\n" + "\n".join(f"- {p}" for p in found[:SHOWN]),
                              {"paths": found})
        if action == "sort":
            ctx.use(self.name, "move_files")
            plan = index.sort_plan()
            total = sum(len(v) for v in plan.values())
            if not total:
                return StepResult("done", f"There are no read photos left to sort. ({status}.)")
            parts = ", ".join(f"{folder} {len(v)}" for folder, v in sorted(plan.items(), key=lambda kv: -len(kv[1])))
            return ctx.ask("photo_sort", f"Move {total} photos into folders in Pictures ({parts})", {"plan": plan},
                           exact=f"sort:{total}:{hash(parts)}",
                           waiting=f"I'd move {total} photos into folders in Pictures: {parts}. Go ahead? "
                                   "(Answer below; it can be undone on the History page.)")
        return StepResult("failed", "I can: find, sort, status.")
