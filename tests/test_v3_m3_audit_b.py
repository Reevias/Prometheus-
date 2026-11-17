"""M3 audit fix plan, Phase B (medium items). Each test failed before its fix."""
from __future__ import annotations

from prometheus.agents.base import Agent, StepResult
from prometheus.models.fake import FakeProvider
from prometheus.orchestrator.loop import ThinkActLoop, approval_resolved
from prometheus.orchestrator.planner import Orchestrator, paused_run
from test_orchestrator import PROMPTS
from test_v3_m3_loop import COMPLEX, Finder, act, done


class Asker(Agent):
    name, description, tools = "asker", "needs a yes first", set()

    def run(self, goal, inputs, ctx):
        ctx.approvals.request("noop", "Send the email?", {})
        return StepResult("waiting_approval", "The email is waiting for your approval.")


REG = {"finder": Finder, "asker": Asker}


def build(make_ctx, replies):
    cloud = FakeProvider(replies, name="cloud", is_local=False)
    cloud.is_free = True
    ctx = make_ctx(providers=[cloud], routes={"plan": ["cloud"], "combine": ["cloud"], "research": ["cloud"],
                                              "private": []})
    o = Orchestrator(ctx.store, ctx.router, REG, lambda t, s: ctx, ctx.killswitch, PROMPTS)
    o.front_door, o.loop = True, ThinkActLoop(o)
    return o, ctx, cloud


# ---- B1: the loop waits for a yes (or Carry on) and then really continues ----------------------------------------
def test_b1_loop_waiting_for_a_yes_is_not_done_and_continues_after_it(make_ctx):
    o, ctx, cloud = build(make_ctx, [act("asker", "send it"), done("Sent, all finished.")])
    chat = ctx.store.create_chat("t")
    out = o.run(chat, COMPLEX)
    assert out.status == "paused"                              # not "done" while a yes is waiting
    assert paused_run(ctx.store, out.task_id) is not None      # its place in the loop was kept
    approval = ctx.store.approvals()[0]
    ctx.approvals.register("noop", lambda payload: "email sent")
    ready = []
    ctx.approvals.on_resolved.append(lambda i, st, res: ready.extend(approval_resolved(ctx.store, i, st, res)))
    ctx.approvals.resolve(approval.id, True)
    assert ready == [out.task_id]                              # the yes tells the app this task can carry on
    after = o.resume_paused(out.task_id)                       # what the app does once the yes is given
    assert after.status == "done" and after.answer == "Sent, all finished."
    assert len(cloud.calls) == 2                               # round 1 was not repeated: it carried on
    assert "email sent" in str(cloud.calls[-1])                # the AI is told what the yes did


def test_b1_pause_then_carry_on_continues_from_the_next_round(make_ctx):
    o, ctx, cloud = build(make_ctx, [act("finder", "find"), act("finder", "find more"), done("Finished.")])
    chat = ctx.store.create_chat("t")
    orig = Finder.run

    def pause_after(self, goal, inputs, c):
        o.pausing.add(chat)
        return orig(self, goal, inputs, c)
    Finder.run = pause_after
    try:
        out = o.run(chat, COMPLEX)
    finally:
        Finder.run = orig
    assert out.status == "paused" and len(ctx.store.steps(out.task_id)) == 1
    assert paused_run(ctx.store, out.task_id) is not None
    after = o.resume_paused(out.task_id)
    assert after.status == "done" and after.answer == "Finished."
    assert [s.goal for s in ctx.store.steps(out.task_id)] == ["Round 1: find", "Round 2: find more"]
