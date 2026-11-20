"""The owner's first laptop test of M3 (20 Nov 2026). Each test failed before its fix."""
from __future__ import annotations

import json

import pytest

from prometheus.agents.base import StepResult
from prometheus.orchestrator.planner import PlanError, resolve_inputs


def _results():
    return {3: StepResult("done", "Plants turn light into sugar.", {"title": "Photosynthesis 101"}),
            6: StepResult("done", "Best one: video 2", {"best_title": "Light reactions"})}


def test_a_step_result_inside_text_is_filled_in():
    """Bug: a note was saved with the words '$step6.best_summary' in it."""
    out = resolve_inputs({"content": "# Summary\n$step3.summary\n\nSource: $step6.best_title ($step3.title)"},
                         _results())
    assert out["content"] == "# Summary\nPlants turn light into sugar.\n\nSource: Light reactions (Photosynthesis 101)"


def test_a_missing_result_inside_text_stops_the_step():
    with pytest.raises(PlanError, match=r"\$step6\.best_summary"):
        resolve_inputs({"content": "Summary: $step6.best_summary"}, _results())


def test_private_results_inside_text_keep_the_privacy_rule():
    with pytest.raises(PlanError, match="private"):
        resolve_inputs({"query": "search for $step3.summary"}, _results(), frozenset({3}), allow_private=False)
    out = resolve_inputs({"content": "about $step3.summary"}, _results(), frozenset({3}))
    assert out["_from_private"] is True


def test_results_never_go_into_code_they_go_into_an_input_file():
    """Bug: the script had `$step3.summary` pasted in and failed with a SyntaxError. Pasting outside text into code
    would also let a web page write code, so the data travels in inputs.json instead."""
    code = "rows = [('Video 1', $step3.summary), ('Best', \"$step6.best_title\")]\nprint(rows)"
    out = resolve_inputs({"code": code}, _results())
    assert "$step" not in out["code"] and "Plants turn" not in out["code"]
    assert 'INPUTS["step3.summary"]' in out["code"] and 'INPUTS["step6.best_title"]' in out["code"]
    assert out["_code_inputs"] == {"step3.summary": "Plants turn light into sugar.",
                                   "step6.best_title": "Light reactions"}


def test_the_script_reads_its_inputs_file(tmp_path):
    from prometheus import sandbox
    from prometheus.agents.code_agent import with_inputs

    code, extra = with_inputs('print(INPUTS["step3.summary"].upper())', {"step3.summary": "it's \"quoted\" text"})
    result, _ = sandbox.run(code, tmp_path, use_windows_sandbox=False, extra=extra)
    assert result.ok and "IT'S \"QUOTED\" TEXT" in result.output, result.output
    assert json.loads(extra["inputs.json"]) == {"step3.summary": "it's \"quoted\" text"}


def test_loop_round_references_inside_text_become_step_references():
    from prometheus.orchestrator.loop import _round_refs

    assert _round_refs({"content": "see $round2.summary and $round3.url"}) == \
        {"content": "see $step2.summary and $step3.url"}


# -- videos: "find 3 YouTube videos on photosynthesis, summarise the best one" summarised nothing ---------------------

def _opener(opened, **kw):
    from prometheus.agents.opener import Opener

    return Opener(launcher=opened.append, **kw)


def _model_ctx(make_ctx):
    from prometheus.models.fake import FakeProvider

    model = FakeProvider(["Plants use light to make sugar."], name="m")
    return make_ctx(providers=[model], routes={"research": ["m"], "private": ["m"]}), model


def test_summarising_a_video_does_not_open_browser_tabs(make_ctx):
    """Bug: each summary opened the video in the browser too (three tabs for one request)."""
    ctx, _ = _model_ctx(make_ctx)
    opened = []
    res = _opener(opened, transcript=lambda vid: "light energy sugar").run(
        "summarise", {"kind": "youtube", "target": "https://youtu.be/dQw4w9WgXcQ", "summarise": "true"}, ctx)
    assert opened == [] and "sugar" in res.summary
    _opener(opened).run("play", {"kind": "youtube", "target": "https://youtu.be/dQw4w9WgXcQ"}, make_ctx())
    assert opened == ["https://youtu.be/dQw4w9WgXcQ"]           # playing still opens it


def test_a_link_inside_a_list_or_text_is_used(make_ctx):
    """Bug: a plan handed over a list of found videos; the text of the list was searched for, so a random video came
    back. The first YouTube link in the text is used instead."""
    ctx, _ = _model_ctx(make_ctx)
    seen = []
    target = '["https://www.youtube.com/watch?v=abcdefghijk", "https://www.youtube.com/watch?v=zzzzzzzzzzz"]'
    res = _opener([], find_video=lambda q: seen.append(q), transcript=lambda vid: seen.append(vid) or "words").run(
        "summarise", {"kind": "youtube", "target": target, "summarise": "true"}, ctx)
    assert seen == ["abcdefghijk"] and res.data["url"] == "https://www.youtube.com/watch?v=abcdefghijk"


def test_video_search_falls_back_to_youtube_itself(monkeypatch):
    """Bug: when the web search found nothing (or failed), there was no video to summarise."""
    from prometheus.agents import opener

    monkeypatch.setattr(opener, "_search_web", lambda q: [])
    page = '..."videoId":"Ph0t0synth1"...,"videoId":"other000001"'
    monkeypatch.setattr(opener, "_youtube_results_page", lambda q: page)
    assert opener.find_video("photosynthesis") == "https://www.youtube.com/watch?v=Ph0t0synth1"


def test_find_videos_gives_several_for_find_3(monkeypatch):
    from prometheus.agents import opener

    monkeypatch.setattr(opener, "_search_web", lambda q: ["https://www.youtube.com/watch?v=aaaaaaaaaaa"])
    monkeypatch.setattr(opener, "_youtube_results_page",
                        lambda q: '"videoId":"aaaaaaaaaaa" "videoId":"bbbbbbbbbbb" "videoId":"ccccccccccc"')
    assert opener.find_videos("photosynthesis", 3) == [
        "https://www.youtube.com/watch?v=aaaaaaaaaaa", "https://www.youtube.com/watch?v=bbbbbbbbbbb",
        "https://www.youtube.com/watch?v=ccccccccccc"]


def test_a_summary_that_could_not_be_made_is_not_done(make_ctx):
    def no_captions(vid):
        raise RuntimeError("no captions")

    res = _opener([], transcript=no_captions).run(
        "summarise", {"kind": "youtube", "target": "https://youtu.be/dQw4w9WgXcQ", "summarise": "true"}, make_ctx())
    assert res.status == "failed" and "no captions" in res.summary


# -- "run python to make a bar chart of 5 random numbers" gave the code as text --------------------------------------

@pytest.mark.parametrize("text", ["run python to make a bar chart of 5 random numbers",
                                  "write and run a script that makes a pie chart of my spending",
                                  "open youtube and play lofi music"])
def test_one_job_with_several_action_words_runs_a_plan(text):
    from prometheus.orchestrator.front_door import by_rules

    d = by_rules(text)
    assert d is not None and d.route in ("medium", "complex"), d


def test_the_quick_ai_cannot_turn_a_tool_job_into_a_plain_answer(monkeypatch):
    from prometheus.orchestrator import front_door

    monkeypatch.setattr(front_door, "by_rules", lambda text: None)
    monkeypatch.setattr(front_door, "by_ai", lambda router, text, history=None: front_door.Decision("simple", "ai"))
    assert front_door.decide(None, "plot a graph of y = x squared").route == "medium"
    assert front_door.decide(None, "why is the sky blue").route == "simple"


def test_a_chart_the_script_made_shows_under_its_answer(app_client, tmp_path):
    """Bug: the chart only appeared as a small file in the side panel; the answer looked like it gave nothing."""
    import time

    from conftest import login

    client, services = app_client()
    login(client)
    store = services.store
    chat = store.create_chat("chart")
    store.add_message(chat, "user", "run python to make a bar chart")
    task = store.create_task(chat, "run python to make a bar chart")
    first = store.add_message(chat, "assistant", "I'd like to run a short Python script.")
    time.sleep(0.01)
    png = tmp_path / "chart.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    store.add_output(task, str(png), "created")
    out_id = store.outputs(chat)[0]["id"]
    second = store.add_message(chat, "assistant", "It worked.\nFiles made: chart.png", "approval")
    page = client.get(f"/c/{chat}/messages").text
    block = page.split(f'id="m{second}"', 1)[1].split('class="msg ', 1)[0]
    assert f"/api/v3/outputs/{out_id}/file" in block and 'class="msg-images"' in block
    earlier = page.split(f'id="m{first}"', 1)[1].split(f'id="m{second}"', 1)[0]
    assert f"/api/v3/outputs/{out_id}/file" not in earlier


# -- "how much did I spend on food last month" answered "Nothing recorded this month yet." ----------------------------

def _money(make_ctx):
    import time as _t

    from prometheus.money import Money

    now = _t.mktime((2026, 11, 20, 20, 0, 0, 0, 0, -1))
    m = Money(make_ctx().store, clock=lambda: now)
    m.add(300, "Swiggy", when=_t.mktime((2026, 10, 12, 13, 0, 0, 0, 0, -1)))       # food, last month
    m.add(120, "Uber", when=_t.mktime((2026, 10, 14, 9, 0, 0, 0, 0, -1)))         # travel, last month
    m.add(80, "Zomato", when=_t.mktime((2026, 11, 18, 21, 0, 0, 0, 0, -1)))       # food, this month
    return m


def test_spending_by_period_and_category(make_ctx):
    m = _money(make_ctx)
    assert "₹300" in m.spent("last month", "Food") and "October" in m.spent("last month", "Food")
    assert "₹420" in m.spent("last month")
    assert "₹80" in m.spent("this month", "Food")
    assert "Nothing" in m.spent("last month", "Health")


@pytest.mark.parametrize("text, period, cat", [
    ("how much did I spend on food last month", "last month", "Food"),
    ("how much did i spend this week", "this week", ""),
    ("how much have I spent on travel this year", "this year", "Travel"),
    ("how much did I spend today", "today", ""),
    ("how much did I spend", "this month", ""),
])
def test_the_spending_shortcut_keeps_period_and_category(text, period, cat):
    from prometheus.quick import understand

    q = understand(text)
    step = q.steps[0]
    assert step["agent"] == "money" and step["inputs"]["action"] == "spent"
    assert step["inputs"]["period"] == period and step["inputs"].get("category", "") == cat
