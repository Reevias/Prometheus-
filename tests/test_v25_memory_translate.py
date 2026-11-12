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
