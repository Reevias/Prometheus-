import os
import time

import pytest

from prometheus.agents.clipboard_agent import ClipboardAgent
from prometheus.clipboard import ClipMemory, is_secret, when_from
from prometheus.quick import understand

NOW = time.mktime((2026, 11, 11, 11, 0, 0, 0, 0, -1))


@pytest.mark.parametrize("text", ["hunter2!Xy", "4111 1111 1111 1111", "123456", "password: sunshine99",
                                  "sk-ant-abcdefghijklmnop", "ghp_abcdefghijklmnop1234"])
def test_secrets_are_never_kept(text):
    assert is_secret(text)


@pytest.mark.parametrize("text", ["https://arxiv.org/abs/2401.00001", "Newton's second law F = ma",
                                  "Meet at 5pm near the library"])
def test_ordinary_text_is_kept(text):
    assert not is_secret(text)


def test_clipboard_screenshots_find_and_prune(make_ctx, monkeypatch):
    import prometheus.clipboard as cb

    ctx = make_ctx()
    copied = ["https://arxiv.org/abs/2401.00001", "https://arxiv.org/abs/2401.00001", "MyP@ssw0rd!",
              "Physics lab report due Friday"]
    now = [NOW]
    shots = ctx.home / "Pictures" / "Screenshots"
    shots.mkdir(parents=True)
    mem = ClipMemory(ctx.store, ctx.home, None, reader=lambda: copied.pop(0), ocr=lambda p: "Invoice 4521 total 3400",
                     clock=lambda: now[0])
    for _ in range(4):
        mem.check_clipboard()
    rows = mem.find()
    assert [r["kind"] for r in rows] == ["text", "link"]                 # the duplicate and the password skipped
    shot = shots / "Screenshot 2026-11-11.png"
    shot.write_bytes(b"x")
    os.utime(shot, (NOW + 5, NOW + 5))
    now[0] = NOW + 10
    assert mem.check_screenshots() == 1 and mem.check_screenshots() == 0
    monkeypatch.setattr(cb, "CURRENT", mem)
    out = ClipboardAgent().run("x", {"kind": "link", "when": "this morning"}, ctx)
    assert "arxiv.org" in out.summary and "Physics" not in out.summary
    out = ClipboardAgent().run("x", {"query": "invoice"}, ctx)
    assert "Invoice 4521" in out.summary and "Screenshot" in out.summary
    now[0] = NOW + 8 * 86400
    mem.prune()
    assert mem.find() == []


def test_spoken_phrases():
    step = understand("what was that link I copied this morning").steps[0]
    assert step["agent"] == "clipboard" and step["inputs"]["kind"] == "link" and "morning" in step["inputs"]["when"]
    assert when_from("yesterday", NOW) == NOW - 11 * 3600 - 86400
