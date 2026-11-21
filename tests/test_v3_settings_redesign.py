"""Settings redesign (owner, 21 Nov 2026): categories, working search, advanced folded away, nothing lost.
Each test failed before its change."""
from __future__ import annotations

import re

from conftest import login


def test_a_small_settings_form_changes_only_what_it_sends(app_client):
    """The big General form is split into small forms in their own categories. A form that sends only some fields
    must leave the others as they were (before, a missing Wi-Fi box meant "off" and the cheaper model was emptied)."""
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    services.settings.allow_lan = True
    services.settings.openrouter_fast_model = "some/fast-model"
    services.settings.step_tries = 4
    r = client.post("/settings", data={"budget_usd": "7", "task_budget_usd": "0.5"}, headers=h)
    assert r.status_code == 200
    s = services.settings
    assert (s.budget_usd, s.task_budget_usd) == (7.0, 0.5)
    assert s.allow_lan is True and s.openrouter_fast_model == "some/fast-model" and s.step_tries == 4
    client.post("/settings", data={"allow_lan_form": "1"}, headers=h)          # the Wi-Fi box, unticked
    assert services.settings.allow_lan is False and services.settings.budget_usd == 7.0
    r = client.post("/settings", data={"budget_usd": "abc"}, headers=h)
    assert r.status_code == 400 and services.settings.budget_usd == 7.0


def test_the_menu_matches_the_sections_and_advanced_is_marked(app_client):
    """Each section is listed under the category it belongs to, and advanced ones say so in the menu and on the
    section (they fold under "Show advanced settings")."""
    client, _ = app_client()
    login(client)
    page = client.get("/settings").text
    sections = dict(re.findall(r'<section class="set( adv)?" id="([^"]+)" data-tab="([a-z]+)"', page) and
                    [(sid, (tab, bool(adv))) for adv, sid, tab in
                     re.findall(r'<section class="set( adv)?" id="([^"]+)" data-tab="([a-z]+)"', page)])
    menu = {sid: (tab, bool(adv)) for sid, tab, adv in
            re.findall(r'<li><a href="#([^"]+)" data-tab="([a-z]+)"( data-adv="1")?>', page)}
    assert sections == menu
    assert sum(adv for _, adv in menu.values()) >= 5
    assert 'id="set-adv-toggle"' in page and 'aria-expanded="false"' in page
    assert 'hx-confirm="Log out of Prometheus on this screen?"' in page             # a consequence, so it asks


def test_the_github_and_wifi_settings_live_in_their_own_categories(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    page = client.get("/settings").text
    github = page.split('id="github"', 1)[1].split("</section>", 1)[0]
    assert 'name="github_repo"' in github
    wifi = page.split('id="wifi-access"', 1)[1].split("</section>", 1)[0]
    assert 'name="allow_lan_form"' in wifi and 'name="allow_lan"' in wifi
    r = client.post("/settings", data={"github_repo": "someone/other"}, headers=h)
    assert r.status_code == 200 and services.settings.github_repo == "someone/other"
    assert client.post("/settings", data={"github_repo": "not a repo"}, headers=h).status_code == 400
