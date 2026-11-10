import sqlite3

from prometheus.memory.chunking import chunk_text, split_spans
from prometheus.memory.store import Store

ESSAY = "\n\n".join(
    f"# Part {n}\n\n" + " ".join(f"Sentence {n}.{i} about topic{n} and its details." for i in range(40))
    for n in range(1, 6))


def test_spans_cover_text_and_respect_size():
    spans = split_spans(ESSAY, 500)
    assert spans[0][0] == 0 and spans[-1][1] == len(ESSAY)
    assert all(b - a <= 500 for a, b in spans)
    assert all(a == prev_b for (_, prev_b), (a, _) in zip(spans, spans[1:]))  # no gaps, no repeats


def test_headings_start_parents_and_children_overlap():
    parents = chunk_text(ESSAY, child_chars=400, parent_chars=2000)
    assert all(len(p.text) <= 2000 for p in parents)
    assert parents[0].text.startswith("# Part 1")
    kids = parents[0].children
    assert len(kids) > 2 and all(len(k.text) <= 460 for k in kids)
    assert kids[1].start < kids[0].end                 # overlap with the chunk before
    assert all(p.start <= k.start and k.end <= p.end for p in parents for k in p.children)
    assert all(ESSAY[k.start:k.end] == k.text for p in parents for k in p.children)


def test_one_huge_word_is_hard_cut():
    parents = chunk_text("x" * 9000, child_chars=1000, parent_chars=4000)
    assert sum(len(p.text) for p in parents) == 9000
    assert all(len(k.text) <= 1150 for p in parents for k in p.children)


def test_empty_text_has_no_chunks():
    assert chunk_text("") == [] and chunk_text("   \n\n ") == []


def test_search_returns_small_match_with_parent_context(tmp_path):
    store = Store(tmp_path / "m.db")
    store.add_document("web:a", "Essay", "https://example.com/a", ESSAY, private=False)
    hits = store.search_chunks("topic3 details", limit=2)
    assert hits and "topic3" in hits[0].match
    assert len(hits[0].context) > len(hits[0].match) and hits[0].match in hits[0].context
    assert hits[0].source == "https://example.com/a"


def test_private_chunks_hidden_unless_asked(tmp_path):
    store = Store(tmp_path / "m.db")
    store.add_document("file:/tax.pdf", "tax.pdf", "/tax.pdf", "My salary slip shows income of 52000.", True)
    assert store.search_chunks("salary income") == []
    assert store.search_chunks("salary income", include_private=True)[0].private
    assert store.recall("salary", kinds=("chunk",)) == []      # recall never shows private file text


def test_same_text_is_not_saved_twice_and_changes_replace(tmp_path):
    store = Store(tmp_path / "m.db")
    a = store.add_document("web:a", "A", "u", ESSAY, False)
    n = store.documents()[0].chunks
    assert store.add_document("web:a", "A", "u", ESSAY, False) == a and store.documents()[0].chunks == n
    store.add_document("web:a", "A", "u", "Completely new words about penguins.", False)
    assert store.search_chunks("topic3") == [] and store.search_chunks("penguins")
    store.delete_document(a)
    assert store.documents() == [] and store.search_chunks("penguins") == []


def test_best_passages_picks_relevant_part_in_order(tmp_path):
    store = Store(tmp_path / "m.db")
    doc = store.add_document("web:a", "A", "u", ESSAY, False)
    text = store.best_passages(doc, "topic4", 4500)
    assert "topic4" in text and len(text) <= 4600
    assert store.best_passages(doc, "", 3000).startswith("# Part 1")


class FakeEmbedder:
    model = "fake"

    def embed(self, texts):
        # meaning by hand: anything about cars or vehicles points the same way
        return [[1.0, 0.0] if any(w in t.lower() for w in ("car", "vehicle", "automobile")) else [0.0, 1.0]
                for t in texts]


def test_meaning_search_finds_synonyms(tmp_path):
    store = Store(tmp_path / "m.db")
    store.embedder = FakeEmbedder()
    store.add_document("web:a", "Cars", "u", "The automobile was invented in 1886.", False)
    store.add_document("web:b", "Food", "u", "Bread is baked from flour.", False)
    hits = store.search_chunks("vehicle history")         # no shared words with the right page
    assert hits and hits[0].title == "Cars"


def test_broken_embedder_falls_back_to_words(tmp_path):
    class Broken:
        model = "x"

        def embed(self, texts):
            raise RuntimeError("no model")

    store = Store(tmp_path / "m.db")
    store.embedder = Broken()
    store.add_document("web:a", "A", "u", "Penguins live in the south.", False)
    assert store.search_chunks("penguins")[0].title == "A"


def test_old_database_gets_chunk_tables(tmp_path):
    path = tmp_path / "m.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE memories (id INTEGER PRIMARY KEY, text TEXT NOT NULL, created REAL NOT NULL)")
    db.execute("INSERT INTO memories (text, created) VALUES ('likes tea', 0)")
    db.commit()
    db.close()
    store = Store(path)
    store.add_document("web:a", "A", "u", "Tea grows in Assam.", False)
    assert store.recall("tea", kinds=("memory",)) and store.search_chunks("assam")


def test_embedder_refuses_remote_ollama():
    import pytest

    from prometheus.models.ollama import OllamaEmbedder

    with pytest.raises(ValueError):
        OllamaEmbedder("nomic-embed-text", "http://10.0.0.5:11434")
    with pytest.raises(ValueError):
        OllamaEmbedder("some-embed:cloud", "http://127.0.0.1:11434")


def test_documents_agent_saves_private_chunks_and_sends_best_part(make_ctx, tmp_home):
    from prometheus.agents.documents import Documents
    from prometheus.models.fake import FakeProvider

    seen = []

    def judge(messages, system):
        seen.append(messages[0]["content"])
        return '{"relevant": true, "summary": "ok"}'

    filler = "\n\n".join(f"Paragraph {i} talks about gardening and weather." for i in range(300))
    doc = tmp_home.home / "notes.txt"
    doc.write_text(filler + "\n\nThe passport number renewal is due in March.\n\n" + filler)
    local = FakeProvider(judge, name="ollama")
    ctx = make_ctx([local])
    Documents().run("find", {"paths": [str(doc)], "question": "passport renewal"}, ctx)
    assert "passport number renewal" in seen[0] and len(seen[0]) < 8000
    assert ctx.store.search_chunks("passport") == []           # private: hidden from cloud-facing search
    assert ctx.store.search_chunks("passport", include_private=True)


def test_memory_page_lists_and_deletes_documents(app_client):
    from tests.conftest import login

    client, services = app_client()
    csrf = login(client)
    doc = services.store.add_document("web:x", "Solar guide", "https://example.com/s", "Solar panels need sun.", False)
    page = client.get("/memory")
    assert "Solar guide" in page.text
    assert "Solar panels need sun" in client.get("/memory?q=solar").text
    resp = client.post(f"/documents/{doc}/delete", headers={"X-CSRF-Token": csrf})
    assert resp.status_code == 200 and "Solar guide" not in resp.text
