import json

from prometheus.agents.homework_agent import HomeworkAgent, newest_photo
from prometheus.quick import understand

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")
CHECK = {"question": "Solve 2x + 3 = 11", "steps": [
    {"written": "2x = 11 + 3", "ok": False, "comment": "sign mistake when moving 3 across"},
    {"written": "x = 7", "ok": False, "comment": "follows from the earlier slip"}],
    "first_mistake": 0, "hint": "What happens to +3 when it moves to the other side?", "overall": "Good layout!"}


class Router:
    def __init__(self):
        self.calls, self.routes = [], {}

    def complete(self, purpose, messages, system, json_schema=None, max_tokens=0):
        self.calls.append((purpose, messages))
        text = json.dumps(CHECK) if purpose == "vision" else "Your notes: move a term across and change its sign."
        return type("R", (), {"text": text})()


def test_photo_goes_to_claude_notes_stay_local(make_ctx):
    ctx = make_ctx()
    ctx.router = Router()
    (ctx.home / "Pictures").mkdir()
    photo = ctx.home / "Pictures" / "hw.png"
    photo.write_bytes(PNG)
    (ctx.home / "algebra.txt").write_text("Transposition: change the sign when moving a term.")
    res = HomeworkAgent().run("x", {"path": "latest", "notes": [str(ctx.home / "algebra.txt")]}, ctx)
    assert res.status == "done" and "✗ 2x = 11 + 3" in res.summary and "Hint" in res.summary
    assert "x = 4" not in res.summary                                     # never the answer
    vision, local = ctx.router.calls
    assert vision[0] == "vision" and ctx.router.routes["vision"] == ["anthropic"]
    assert vision[1][0]["content"][0]["type"] == "image"
    assert "Transposition" not in json.dumps(vision[1])                   # notes never go to Claude
    assert local[0] == "private" and "From your notes" in res.summary


def test_latest_photo_and_phrase(tmp_path):
    (tmp_path / "Downloads").mkdir()
    (tmp_path / "Downloads" / "a.jpg").write_bytes(PNG)
    assert newest_photo(tmp_path).name == "a.jpg"
    assert understand("check my homework").steps[0]["inputs"]["path"] == "latest"
