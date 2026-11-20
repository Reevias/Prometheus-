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
