from prometheus.lectures import LectureJob, notes_from
from prometheus.models.fake import FakeProvider

TRANSCRIPT = ("Today we study Newton's laws.\nThe first law says a body stays at rest unless a force acts.\n"
              "The second law is F equals m a.\n") * 3


class Router:
    def __init__(self):
        self.routes = []

    def complete(self, purpose, messages, system, json_schema=None, max_tokens=0):
        self.routes.append(purpose)
        if json_schema:
            return FakeProvider(['{"cards": [{"question": "Second law?", "answer": "F = ma"}]}']).complete(messages,
                                                                                                         system)
        return FakeProvider(["## Newton's laws\n- **First law**: inertia\n- **Second law**: F = ma"]).complete(
            messages, system)


def test_lecture_makes_transcript_notes_slides_cards(app_client):
    _, services = app_client()
    router = Router()
    services.router = router
    from prometheus.agents import study_agent

    study_agent.STUDY.router = router
    rec = services.home / "physics class.m4a"
    rec.write_bytes(b"fake audio")
    chat = services.store.create_chat("c")
    task = services.store.create_task(chat, "x")
    made = LectureJob(services, rec, chat, task, transcriber=lambda p, f, progress: TRANSCRIPT).run()
    assert made["transcript"].read_text().startswith("Today we study")
    assert made["notes"].read_bytes()[:4] == b"%PDF" and made["slides"].suffix == ".pptx" and made["cards"] == 1
    assert set(router.routes) == {"private"}                     # a recording is private: the local AI writes notes
    last = services.store.messages(chat)[-1].text
    assert "physics class.m4a is done" in last and "quiz me on physics class" in last


def test_long_transcripts_are_split():
    router = Router()
    notes = notes_from("A sentence about optics.\n" * 2000, router, "private", "Optics")
    assert notes.startswith("# Optics") and len(router.routes) >= 5


def test_agent_refuses_non_audio(make_ctx, monkeypatch):
    import prometheus.agents.lecture_agent as la

    ctx = make_ctx()
    monkeypatch.setattr(la, "SERVICES", object())
    (ctx.home / "notes.pdf").write_bytes(b"%PDF")
    assert la.LectureAgent().run("x", {"path": str(ctx.home / "notes.pdf")}, ctx).status == "failed"
