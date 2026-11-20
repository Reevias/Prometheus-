"""The owner's second laptop check (20 Nov 2026, evening). Each test failed before its fix."""
from __future__ import annotations

import pytest

from conftest import login


# -- "forgets my name" ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("change", ["name", "appearance", "quick"])
def test_settings_changes_keep_the_same_settings_so_nothing_is_wiped_later(app_client, change):
    """Bug: saving the name replaced the settings object; the model switch (and others) later saved the OLD object,
    which had no name, so the name disappeared."""
    from prometheus.config import load_settings, save_settings

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    old = services.settings
    client.post("/api/v3/profile", data={"name": "Saiveer"}, headers=h)
    if change == "appearance":
        client.post("/api/v3/appearance", data={"ui_glow": "1.2", "ui_text": "16"}, headers=h)
    if change == "quick":
        client.post("/api/v3/quick-actions", json={"actions": []}, headers=h)
    assert services.settings is old
    save_settings(old)                       # what the model-switch approval does with the copy it holds
    assert load_settings().owner_name == "Saiveer"


def test_the_name_box_is_part_of_the_page_not_a_browser_popup(app_client):
    """prompt() may not show in the desktop window; the name box is the page's own."""
    client, _ = app_client()
    login(client)
    page = client.get("/").text
    assert 'id="name-dialog"' in page and 'name="name"' in page
    js = client.get("/static/v3/app.js").text
    assert 'prompt("What should Prometheus call you?")' not in js
