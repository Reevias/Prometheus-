import time

from prometheus import undo
from prometheus.agents.files import Files
from prometheus.agents.reminders_agent import Reminders as RemindersAgent
from prometheus.agents.undo_agent import UndoAgent
from prometheus.quick import understand


def services_for(ctx, monkeypatch, facts=None):
    s = type("S", (), {})()
    s.store, s.files, s.journal = ctx.store, ctx.files, ctx.files.journal
    s.orchestrator = type("O", (), {"facts": facts})()
    monkeypatch.setattr(undo, "SERVICES", s)
    ctx.approvals.register("undo", undo.handler)
    return s


def answer_yes(ctx):
    ask = [a for a in ctx.store.asks_for_chat(ctx.chat_id()) if a.kind == "undo"][-1]
    return ctx.approvals.resolve(ask.id, approved=True)


def test_undo_a_reminder_after_yes(make_ctx, monkeypatch):
    ctx = make_ctx()
    services_for(ctx, monkeypatch)
    RemindersAgent().run("x", {"action": "set", "text": "call mom", "when": "in 2 hours"}, ctx)
    res = UndoAgent().run("x", {"action": "undo"}, ctx)
    assert res.status == "waiting_approval" and ctx.store.reminders()                 # nothing before a yes
    assert "call mom" in answer_yes(ctx) and not ctx.store.reminders()
    assert "nothing" in UndoAgent().run("x", {}, ctx).summary.lower()


def test_undo_moves_files_back(make_ctx, monkeypatch):
    ctx = make_ctx()
    services_for(ctx, monkeypatch)
    (ctx.home / "Downloads").mkdir()
    (ctx.home / "Documents").mkdir()
    f = ctx.home / "Downloads" / "notes.txt"
    f.write_text("hi")
    Files().run("x", {"action": "move", "paths": [str(f)], "to": str(ctx.home / "Documents")}, ctx)
    assert not f.exists()
    res = UndoAgent().run("x", {}, ctx)
    assert "notes.txt" in ctx.store.asks_for_chat(ctx.chat_id())[-1].summary
    answer_yes(ctx)
    assert f.exists()


def test_old_actions_and_email(make_ctx, monkeypatch):
    ctx = make_ctx()
    s = services_for(ctx, monkeypatch)
    undo.record(ctx.store, "email", "the email to asha@example.com", {})
    assert "can't be unsent" in UndoAgent().run("x", {}, ctx).summary
    ctx.store._write("UPDATE undo_log SET created = ?", (time.time() - 7200,))
    assert undo.last(s) is None                                                      # older than an hour


def test_undo_saved_facts(make_ctx, monkeypatch):
    from prometheus.memory.facts import Facts

    ctx = make_ctx()
    facts = Facts(ctx.store)
    services_for(ctx, monkeypatch, facts)
    fid, _ = facts.save("Ravi", "relation", "brother")
    undo.record(ctx.store, "fact", "remembering Ravi is your brother", {"ids": [fid]})
    UndoAgent().run("x", {}, ctx)
    answer_yes(ctx)
    assert not facts.about("Ravi")


def test_spoken_undo_is_instant():
    for said in ("undo that", "Undo", "take that back", "undo the last thing"):
        assert understand(said).steps[0]["agent"] == "undo"
