from pathlib import Path

from prometheus import photos
from prometheus.agents.photos_agent import PhotosAgent

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")


class FakeLooker:
    SEEN = {"goa.jpg": (["beach", "people"], ""), "aadhaar.jpg": (["people"], "Government of India Aadhaar 1234 5678 9012"),
            "Screenshot 2026.png": (["meme"], "Battery 20%"), "dinner.jpg": (["food"], "")}

    def tags(self, path):
        return list(self.SEEN[Path(path).name][0])

    def text(self, path):
        return self.SEEN[Path(path).name][1]


def test_reads_tags_finds_and_sorts_after_yes(make_ctx):
    ctx = make_ctx()
    pics = ctx.home / "Pictures"
    pics.mkdir()
    for name in FakeLooker.SEEN:
        (pics / name).write_bytes(PNG)
    index = photos.PhotoIndex(ctx.store, ctx.policy, looker=FakeLooker(), pause=0)
    photos.CURRENT = index
    assert index.run()["read"] == 4
    tags = index.tagged()
    assert tags[str(pics / "aadhaar.jpg")][0] == "document"          # lots of text: a document
    assert tags[str(pics / "Screenshot 2026.png")][0] == "screenshot"   # named like a screenshot
    agent = PhotosAgent()
    assert "goa.jpg" in agent.run("x", {"action": "find", "query": "my beach photos"}, ctx).summary
    assert "aadhaar.jpg" in agent.run("x", {"action": "find", "query": "Aadhaar"}, ctx).summary
    ctx.approvals.register("photo_sort", lambda payload: photos.sort_handler(payload, ctx.policy, ctx.files))
    res = agent.run("x", {"action": "sort"}, ctx)
    assert res.status == "waiting_approval" and (pics / "goa.jpg").exists()     # nothing moves before a yes
    ask = ctx.store.asks_for_chat(ctx.chat_id())[0]
    assert "Beach 1" in ask.summary and "Documents 1" in ask.summary
    out = ctx.approvals.resolve(ask.id, approved=True)
    assert "Moved 4 photos" in out
    assert (pics / "Beach" / "goa.jpg").exists() and (pics / "Documents" / "aadhaar.jpg").exists()
    assert (pics / "Screenshots" / "Screenshot 2026.png").exists() and (pics / "Food" / "dinner.jpg").exists()
    assert not index.sort_plan()                                     # sorted photos stay put next time
    assert "goa.jpg" in agent.run("x", {"action": "find", "query": "beach"}, ctx).summary


def test_missing_models_say_how_to_get_them(make_ctx):
    ctx = make_ctx()
    (ctx.home / "Pictures").mkdir()
    (ctx.home / "Pictures" / "a.jpg").write_bytes(PNG)

    class NoModels:
        def tags(self, path):
            raise ImportError("fastembed")

        text = tags

    index = photos.PhotoIndex(ctx.store, ctx.policy, looker=NoModels(), pause=0)
    assert index.run().get("missing") and "[photos]" in index.status
