"""Review of M6 6.3–6.5 (Opus, 26 Nov 2026). Each test failed before its fix."""
from __future__ import annotations


def test_approvals_waiting_across_a_restart_still_have_a_handler(app_client):
    """pc_control_read / pc_control_input / phone_read were registered only when their agent first ran, so a yes
    given after a restart (before the agent ran again) failed with "No handler"; Stop all's hook for PC control was
    also missing until then."""
    _, services = app_client()
    handlers = services.approvals._handlers
    for kind in ("pc_control_read", "pc_control_input", "phone_read", "phone_action"):
        assert kind in handlers, kind
    assert getattr(services.approvals, "_pc_stop_hooked", False)


# -- 6.4a PC control --------------------------------------------------------------------------------------------------
def test_a_window_title_can_never_break_out_of_the_powershell_script():
    """PowerShell also treats the curly quotes ‘ ’ ‚ ‛ as single quotes; only ' was doubled, so a window title chosen
    by the AI could end the string and run its own command."""
    import re

    from prometheus.pc_control.uia import _build_read_script

    evil = "x’; Start-Process calc; ’ ‘ ‚ ‛ \" ` $(calc)"
    script = _build_read_script(evil, 5)
    assert "calc" not in script and not any(q in script for q in "‘’‚‛")
    m = re.search(r"FromBase64String\('([A-Za-z0-9+/=]*)'\)", script)
    import base64

    assert m and base64.b64decode(m.group(1)).decode("utf-8") == evil.strip()


def test_the_approval_shows_every_character_that_will_be_typed():
    from prometheus.pc_control.agent import _describe_input

    text = "hello there " + "x" * 40 + " rm everything"
    summary, payload = _describe_input("type", {"text": text})
    assert text in summary and payload["text"] == text


def test_keys_that_write_text_are_refused_in_a_password_field(tmp_path):
    """press_keys skipped the password-field check, so "1" or Ctrl+V could go into a password box one key at a time."""
    import pytest

    from prometheus.pc_control.controller import PCController
    from prometheus.pc_control.errors import PasswordFieldError
    from tests.test_v3_m6_pc_control import _make_fake_backend

    backend, state = _make_fake_backend(fg_idx=0)
    ctrl = PCController(data_dir=tmp_path, backend=backend, password_checker=lambda **k: (True, "password box"))
    for keys in ("a", "1", "ctrl+v", "shift+insert", "space"):
        with pytest.raises(PasswordFieldError):
            ctrl.press_keys(keys)
    ctrl.press_keys("tab")                                   # moving on from the box is still fine
    assert [e for e in state["events"] if e[0] == "keys"] and len([e for e in state["events"] if e[0] == "keys"]) == 1


def test_a_shrunk_screenshot_says_how_to_turn_its_pixels_into_screen_points(tmp_path, monkeypatch):
    """Screens wider than 1600 px are shrunk, but clicks use the real screen: the AI must be told the scale."""
    from PIL import Image

    from prometheus.pc_control import agent
    from prometheus.pc_control.controller import PCController
    from tests.test_v3_m6_pc_control import _make_fake_backend

    backend, _ = _make_fake_backend(vscreen=(-1920, 0, 3840, 1080))
    raw = Image.new("RGB", (3840, 1080))
    monkeypatch.setattr(agent, "make_controller", lambda d, killswitch=None: PCController(d, backend=backend, grabber=lambda: raw))
    said = agent._execute_read({"action": "screenshot", "data_dir": str(tmp_path)})
    assert "1600x450" in said and "x 2.4" in said and "-1920" in said
