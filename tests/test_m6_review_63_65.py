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
