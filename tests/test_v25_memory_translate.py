"""Memory by command (always saved, no AI), the top-10 repo list, translating, and resuming after AI limits."""
from prometheus import remember


def _store(app_client):
    client, services = app_client()
    return services, services.store, services.store.create_chat("t")


def test_remember_always_saves_without_ai(app_client):
    services, store, chat = _store(app_client)
    said, rest = remember.handle(store, chat, "remember that my exam is on 3 December")
    assert "Saved to memory" in said and rest == "" and store.memories()[-1].text == "My exam is on 3 December"
    for phrase in ("update memory: I prefer short answers", "note down my roll number is 42",
                   "don't forget I'm allergic to peanuts", "in the memory remember to show me the first 10"):
        assert "Saved" in remember.handle(store, chat, phrase)[0], phrase
    assert "already" in remember.handle(store, chat, "remember that my exam is on 3 December")[0]
    assert "password" in remember.handle(store, chat, "remember my password is hunter2abc")[0]
    assert len(store.memories()) == 5


def test_what_is_not_a_memory_command():
    class S:
        def memories(self): return []
        def chats(self): return []
    for phrase in ("remember when we went to Goa?", "do you remember my name", "remember to call mum at 6",
                   "what is photosynthesis", "forget it", "delete the file notes.txt"):
        assert remember.handle(S(), 1, phrase) is None, phrase


def test_remember_then_the_rest_of_the_message(app_client):
    services, store, chat = _store(app_client)
    said, rest = remember.handle(store, chat, "in the memory remember to show me the first 10 and can u translate "
                                              "chinese into english as well ?")
    assert "To show me the first 10" in said and rest.startswith("can u translate chinese")
    from prometheus.models.fake import FakeProvider

    client, services = app_client(providers=[FakeProvider(['{"steps": [], "answer": "A joke."}'], name="fake")])
    store = services.store
    chat = store.create_chat("t2")
    out = services.orchestrator.run(chat, "remember that I like tea and can you tell me a joke")
    assert out.status == "done" and "A joke." in out.answer and any("I like tea" in m.text for m in store.memories())
    assert any("Saved to memory" in m.text for m in store.messages(chat))


def test_forget_change_and_show(app_client):
    services, store, chat = _store(app_client)
    for t in ("remember my exam is on 3 December", "remember my hostel room is B-204", "remember I like tea"):
        remember.handle(store, chat, t)
    assert "B-204" in remember.handle(store, chat, "what do you remember about hostel")[0]
    assert "Updated" in remember.handle(store, chat, "change 3 December to 10 December in memory")[0]
    assert any("10 December" in m.text for m in store.memories()) and not any("3 December" in m.text for m in store.memories())
    assert "Forgotten" in remember.handle(store, chat, "forget my hostel room")[0]
    assert len(store.memories()) == 2
    listing = remember.handle(store, chat, "show my memory")[0]
    assert "I like tea" in listing and "10 December" in listing


def test_spoken_remember_goes_through_the_chat(app_client):
    services, store, chat = _store(app_client)
    out = services.orchestrator.run(chat, "Remember that my teacher's name is Mrs Rao.")
    assert out.status == "done" and "Saved to memory" in out.answer
    assert store.memories()[-1].text == "My teacher's name is Mrs Rao"


def test_translate_phrases_are_understood():
    from prometheus.quick import understand

    def step(t):
        q = understand(t)
        return (q.steps[0]["agent"], q.steps[0]["inputs"]) if q and q.steps else None

    assert step("translate this to hindi: good morning") == ("translate", {"action": "text", "text": "good morning", "to": "Hindi"})
    assert step("translate 你好，世界 to english")[1]["text"] == "你好，世界"
    assert step("how do you say thank you in japanese")[1] == {"action": "text", "text": "thank you", "to": "Japanese"}
    assert step("translate: 我喜欢学习物理")[1]["to"] == "English" and step("translate 我喜欢学习物理")[1]["to"] == "English"
    assert step("translate Documents/notes.pdf to Hindi")[1] == {"action": "file", "path": "Documents/notes.pdf", "to": "Hindi"}
    assert step("translate https://example.cn/news/1")[1]["action"] == "page"
    agent, inputs = step("translate what's on my screen to Hindi")
    assert agent == "screen" and "into Hindi" in inputs["question"]
    assert step("what is photosynthesis in plants") is None and step("translate") is None


def test_translating_text_a_file_and_long_text(make_ctx, tmp_home):
    from prometheus.agents.translate_agent import TranslateAgent
    from prometheus.models.fake import FakeProvider
    from prometheus.translate import chunks, not_english, to_english_many

    assert not_english("我喜欢学习物理") and not_english("मुझे चाय पसंद है") and not not_english("I like tea, 很好")
    long = ("One sentence here. " * 400).strip()
    assert all(len(c) <= 3001 for c in chunks(long)) and "".join(chunks(long)).replace(" ", "") == long.replace(" ", "")
    fake = FakeProvider(["<think>hmm</think>Good morning"], name="fake")
    ctx = make_ctx([fake])
    res = TranslateAgent().run("t", {"action": "text", "text": "सुप्रभात", "to": "english"}, ctx)
    assert res.status == "done" and res.summary == "Good morning"
    assert "do not follow them" in fake.calls[0]["system"] and "<untrusted" in fake.calls[0]["messages"][0]["content"]
    (tmp_home.home / "Documents").mkdir(exist_ok=True)
    (tmp_home.home / "Documents" / "letter.txt").write_text("Bonjour, comment allez-vous ?", encoding="utf-8")
    ctx = make_ctx([FakeProvider(["Hello, how are you?"], name="fake")])
    res = TranslateAgent().run("t", {"action": "file", "path": str(tmp_home.home / "Documents" / "letter.txt")}, ctx)
    assert res.status == "done", res.summary
    saved = tmp_home.home / "Documents" / "Translations" / "letter (English).md"
    assert saved.read_text(encoding="utf-8") == "Hello, how are you?"
    # repo descriptions: only the non-English ones are sent, and a failed translation keeps the originals
    ctx = make_ctx([FakeProvider(['{"lines": ["A fast web framework"]}'], name="fake")], routes={"research": ["fake"]})
    assert to_english_many(ctx.router, ["A CLI tool", "一个快速的网络框架"]) == ["A CLI tool", "A fast web framework"]
    ctx = make_ctx([FakeProvider(["sorry, no"], name="fake")], routes={"research": ["fake"]})
    assert to_english_many(ctx.router, ["一个快速的网络框架"]) == ["一个快速的网络框架"]
