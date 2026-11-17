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


# ---- B2: no keyless provider by default, and none ever sees memories --------------------------------------------
def test_b2_free_no_key_providers_are_off_for_a_new_install():
    from prometheus.config import Settings

    assert Settings().free_no_key is False


def test_b2_a_keyless_provider_gets_the_request_but_no_memory_or_facts(make_ctx):
    keyless = FakeProvider(["plain reply"], name="nokey", is_local=False)
    keyless.is_free, keyless.keyless = True, True
    keyed = FakeProvider(["plain reply"], name="keyed", is_local=False)
    keyed.is_free = True
    for provider, expect_memory in ((keyless, False), (keyed, True)):
        ctx = make_ctx(providers=[provider], routes={"plan": [provider.name], "private": []})
        ctx.store.add_memory("my locker code is 4421")
        o = Orchestrator(ctx.store, ctx.router, REG, lambda t, s: ctx, ctx.killswitch, PROMPTS)
        content = o._context("what is my locker code", None) + "Request: what is my locker code"
        ctx.router.complete("plan", [{"role": "user", "content": content}], "sys")
        sent = str(provider.calls[-1]["messages"])
        assert "Request: what is my locker code" in sent
        assert ("4421" in sent) is expect_memory
        assert "personal-context" not in sent                    # the markers never reach an AI


# ---- B3: the webhook token can ride in a header, and never shows in the access log -------------------------------
import logging  # noqa: E402


def _hook_client(app_client):
    client, services = app_client()
    services.settings.webhook_token = "tok-SECRET-123"
    return client, services


def test_b3_token_in_a_header_works_and_a_wrong_one_is_not_found(app_client):
    client, services = _hook_client(app_client)
    assert client.post("/hooks", json={"text": "hi"}, headers={"X-Webhook-Token": "tok-SECRET-123"}).status_code == 200
    assert client.post("/hooks", json={}, headers={"Authorization": "Bearer tok-SECRET-123"}).status_code == 200
    assert client.post("/hooks", json={}, headers={"X-Webhook-Token": "nope"}).status_code == 404
    assert client.post("/hooks", json={}).status_code == 404
    assert client.post("/hooks/tok-SECRET-123", json={}).status_code == 200          # the old address still works
    assert client.post("/hooks/sms", json={}, headers={"X-Webhook-Token": "nope"}).status_code == 404


def test_b3_the_token_is_hidden_in_the_access_log(caplog):
    import logging.config

    from prometheus.logfilter import install_log_redaction

    install_log_redaction()
    line = '%s - "%s %s HTTP/%s" %d'
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    logging.getLogger("uvicorn.access").info(line, "1.2.3.4:5", "POST", "/hooks/tok-SECRET-123", "1.1", 200)
    logging.getLogger("uvicorn.access").info(line, "1.2.3.4:5", "POST", "/hooks/tok-SECRET-123/sms", "1.1", 200)
    logging.getLogger("uvicorn.access").info(line, "1.2.3.4:5", "GET", "/v3/tasks", "1.1", 200)
    text = caplog.text
    assert "tok-SECRET-123" not in text and "/hooks/***" in text and "/hooks/***/sms" in text
    assert "/v3/tasks" in text                                                       # other lines are untouched


# ---- B4: every saved secret is scrubbed from terminal output (MCP, webhook, sync tokens too) ---------------------
def test_b4_terminal_output_hides_every_saved_token(tmp_home):
    from prometheus import features
    from prometheus.actions.terminal import scrub_secrets
    from prometheus.config import Settings, set_secret
    from prometheus.safety.secrets import find_secrets

    set_secret("MCP_GITHUB", "mcp-token-AAAA1111BBBB")
    set_secret("MCP_SERVER_TOKEN", "mcpserver-CCCC2222DDDD")
    set_secret("SYNC_TOKEN", "sync-token-EEEE3333FFFF")
    old = features.CURRENT
    features.CURRENT = Settings(webhook_token="webhook-GGGG4444HHHH")
    try:
        text = ("a mcp-token-AAAA1111BBBB b mcpserver-CCCC2222DDDD c sync-token-EEEE3333FFFF "
                "d webhook-GGGG4444HHHH e")
        out = scrub_secrets(text)
        for token in ("AAAA1111", "CCCC2222", "EEEE3333", "GGGG4444"):
            assert token not in out
        assert out.startswith("a ") and out.endswith(" e")           # the rest of the text is kept
        assert find_secrets("curl /x/webhook-GGGG4444HHHH") == ["one of your saved keys"]
    finally:
        features.CURRENT = old


def test_b4_a_harmless_env_override_is_still_allowed_and_a_saved_token_is_refused(tmp_home):
    from prometheus.actions.terminal import overrides_allowed
    from prometheus.config import set_secret

    set_secret("MCP_GITHUB", "mcp-token-AAAA1111BBBB")
    assert overrides_allowed("MY_MODE", "fast")
    assert not overrides_allowed("MY_MODE", "x mcp-token-AAAA1111BBBB")


# ---- B5: moving or copying a file Windows would run asks first ---------------------------------------------------
def test_b5_moving_or_copying_a_script_waits_for_a_yes(make_ctx, tmp_home):
    from prometheus.agents.files import Files, copy_move_handler

    ctx = make_ctx()
    home = tmp_home.home
    for rel in ("Downloads/tool.ps1", "Downloads/setup.bat", "Downloads/notes.txt"):
        (home / rel).parent.mkdir(parents=True, exist_ok=True)
        (home / rel).write_text("echo hi")
    moved = Files().run("t", {"action": "move", "paths": ["Downloads/tool.ps1"], "to": "Desktop"}, ctx)
    assert moved.status == "waiting_approval" and (home / "Downloads/tool.ps1").exists()
    copied = Files().run("t", {"action": "copy", "paths": ["Downloads/setup.bat"], "to": "Desktop"}, ctx)
    assert copied.status == "waiting_approval" and not (home / "Desktop/setup.bat").exists()
    pending = ctx.store.big_approvals()
    assert [p.kind for p in pending] == ["copy_move_runnable"] * 2
    out = copy_move_handler(pending[0].payload, ctx.policy, ctx.files)           # what the owner's yes runs
    assert (home / "Desktop/tool.ps1").exists() and not (home / "Downloads/tool.ps1").exists() and "Moved" in out
    plain = Files().run("t", {"action": "move", "paths": ["Downloads/notes.txt"], "to": "Desktop"}, ctx)
    assert plain.status == "done" and (home / "Desktop/notes.txt").exists()        # a normal file still just moves


# ---- B6: the 10-file Recycle limit counts the whole task, not each step ------------------------------------------
def test_b6_recycling_in_small_steps_still_asks_once_the_task_passes_ten(make_ctx, tmp_home):
    from prometheus.agents.files import Files

    trashed = []
    ctx = make_ctx(trash=lambda p: (trashed.append(p), __import__("os").remove(p)))
    home = tmp_home.home
    batches = []
    for b in range(3):
        paths = []
        for i in range(8):
            f = home / "Downloads" / f"junk{b}-{i}.tmp"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("j")
            paths.append(str(f))
        batches.append(paths)
    results = []
    for paths in batches:
        sid = ctx.store.add_step(ctx.task_id, "files", "recycle", {})
        ctx.step_id = sid
        res = Files().run("t", {"action": "recycle", "paths": paths}, ctx)
        ctx.store.finish_step(sid, res.status, res.summary, res.data)
        results.append(res)
    assert results[0].status == "done" and len(trashed) == 8             # 8 so far: under the limit
    assert results[1].status == "waiting_approval"                       # 16 in one task: asks
    assert results[2].status == "waiting_approval"
    assert len(trashed) == 8 and all(__import__("pathlib").Path(p).exists() for p in batches[1] + batches[2])


# ---- B7: Antigravity jobs reserve their worst-case cost when they start ------------------------------------------
def test_b7_two_jobs_cannot_both_pass_a_cap_meant_for_one(tmp_path):
    import pytest

    from prometheus.agents.antigravity import AntigravityRunner
    from prometheus.memory.store import Store
    from prometheus.safety.budget import Budget, BudgetExceeded
    from prometheus.safety.killswitch import KillSwitch
    from test_v13_review2 import Google

    store = Store(tmp_path / "m.db")
    pending = []
    r = AntigravityRunner(store, Budget(store, 1.0), KillSwitch(), client_factory=lambda: Google(),
                          sleep=lambda s: None, run_in_thread=pending.append)       # jobs wait, as if still running
    chat = store.create_chat("c")
    job = {"task": "t", "model": "gemini-3.8-flash", "max_tokens": 200_000, "chat_id": chat}   # about $0.60 worst case
    r.submit(job)
    with pytest.raises(BudgetExceeded):
        r.submit(job)                                         # the first one's $0.60 is already held back
    assert r.reserved_usd > 0
    pending[0]()                                              # the first job finishes: its hold is let go
    assert r.reserved_usd == 0


# ---- B8: a job from the cloud that could change something waits for a yes on the laptop --------------------------
def test_b8_cloud_jobs_that_change_things_wait_for_a_yes(app_client):
    from types import SimpleNamespace

    from prometheus.config import set_secret

    _, services = app_client()
    services.settings.cloud_url = "https://cloud.example"
    set_secret("SYNC_TOKEN", "sync-token-0123456789abcdef")
    jobs = [{"id": 1, "chat_title": "Phone", "text": "what is in my Downloads?", "read_only": 1},
            {"id": 2, "chat_title": "Phone", "text": "delete my old photos", "read_only": 0}]

    class Http:
        def post(self, url, headers=None):
            return SimpleNamespace(status_code=200, json=lambda: {"jobs": jobs})

    started = []
    services.start_task = lambda chat, text, read_only=False: (started.append((text, read_only)),
                                                                SimpleNamespace(join=lambda timeout=None: None))[1]
    services.sync = None
    services.laptop_jobs.http = Http()
    assert services.laptop_jobs.once() == 2
    assert started == [("what is in my Downloads?", True)]                 # reading runs as before
    waiting = services.store.big_approvals()
    assert [a.kind for a in waiting] == ["laptop_job"] and "delete my old photos" in waiting[0].summary
    services.approvals.resolve(waiting[0].id, True)                       # one tap
    assert started[-1] == ("delete my old photos", False)
    rejected_job = {"id": 3, "chat_title": "Phone", "text": "wipe it", "read_only": 0}
    jobs[:] = [rejected_job]
    services.laptop_jobs.once()
    services.approvals.resolve(services.store.big_approvals()[0].id, False)
    assert ("wipe it", False) not in started
