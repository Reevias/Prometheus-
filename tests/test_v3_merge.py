"""Fewer pages (owner, 20 Nov 2026): four sidebar pages; merged pages share a tab bar; Settings in tabs."""
from __future__ import annotations

import re

import pytest

from conftest import login


def _side(client, path="/"):
    page = client.get(path).text
    return page, page.split('id="sidebar"', 1)[1].split("</aside>", 1)[0]


# -- Task 1: page groups and the four-page sidebar -------------------------------------------------------------------------

def test_the_sidebar_shows_four_pages_with_ctrl_1_to_4(app_client):
    client, _ = app_client()
    login(client)
    _, side = _side(client)
    keys = {int(k): h for h, k in re.findall(r'href="([^"]+)"[^>]*data-key="(\d+)"', side)}
    assert keys == {1: "/", 2: "/v3/tasks", 3: "/v3/agents", 4: "/v3/research?mode=study"}
    assert 'id="nav-more-btn"' not in side
    for label in ("Home", "Tasks", "Abilities", "Study &amp; Money"):
        assert label in side


def test_every_other_page_is_still_in_the_ctrl_k_search(app_client):
    client, _ = app_client()
    login(client)
    _, side = _side(client)
    hidden = dict(re.findall(r'<a href="([^"]+)" class="nav-hidden"[^>]*>.*?<span>([^<]+)</span>', side, re.S))
    for href in ("/v3/chats", "/v3/projects", "/v3/skills", "/v3/automations", "/v3/approvals", "/v3/memory",
                 "/v3/system", "/v3/research?mode=research"):
        assert href in hidden, href


@pytest.mark.parametrize("path, group, tab, nav", [
    ("/v3/tasks", "tasks", "Tasks", "/v3/tasks"),
    ("/v3/approvals", "tasks", "Waiting for you", "/v3/tasks"),
    ("/v3/automations", "tasks", "Scheduled", "/v3/tasks"),
    ("/v3/agents", "abilities", "Agents", "/v3/agents"),
    ("/v3/skills", "abilities", "Skills", "/v3/agents"),
    ("/v3/research?mode=study", "study", "Study", "/v3/research?mode=study"),
    ("/v3/research?mode=money", "study", "Money", "/v3/research?mode=study"),
    ("/v3/memory", "settings", "Memory", None),
    ("/v3/system", "settings", "System", None),
])
def test_merged_pages_share_a_tab_bar_and_light_their_group(app_client, path, group, tab, nav):
    client, _ = app_client()
    login(client)
    page, side = _side(client, path)
    bar = page.split(f'class="page-tabs" data-group="{group}"', 1)[1].split("</nav>", 1)[0]
    assert re.search(r'class="on"[^>]*aria-current="page"[^>]*>' + re.escape(tab.replace("&", "&amp;")), bar), bar
    if nav:
        assert re.search(r'href="' + re.escape(nav) + r'" class="on"', side)
    else:                                                       # the Settings group lights the gear
        assert re.search(r'class="icon-btn on" href="/settings"', side)


def test_old_and_new_addresses_open_the_right_place(app_client):
    client, _ = app_client()
    login(client)
    assert client.get("/v3/abilities", follow_redirects=False).headers["location"] == "/v3/agents"
    assert client.get("/v3/study", follow_redirects=False).headers["location"] == "/v3/research?mode=study"
    assert 'data-group="study"' in client.get("/v3/research").text            # Study by default now
    sources = client.get("/v3/research?mode=research").text
    assert 'data-group="settings"' in sources and "Research sources" in sources


def test_study_and_money_have_no_research_tab(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/v3/research?mode=study").text
    assert "mode=research" not in page.split('class="page-tabs"', 1)[1].split("</nav>", 1)[0]


# -- Task 2: Settings in tabs ------------------------------------------------------------------------------------------------

# Settings redesign (owner, 21 Nov 2026): categories instead of tabs; every section still in exactly one of them
TABS = {"general": ["appearance", "features", "notifications", "updates", "about", "log-out"],
        "ai": ["ai", "spending", "costs", "models", "practice-tests"],
        "voice": ["voice"],
        "connections": ["connection-status", "connections", "github", "other-keys", "phone", "cloud-copy", "cloudroom", "obsidian",
                        "use-prometheus-from-other-ai-apps", "webhook"],
        "daily": ["daily-summary", "focus-mode", "assignments", "study-coach", "money-tracker",
                  "clipboard-and-screenshot-memory", "watchers", "search-inside-files"],
        "privacy": ["always-allowed", "places-prometheus-may-use", "backup", "safety-check", "wifi-access"]}


def test_every_settings_section_belongs_to_exactly_one_tab(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/settings").text
    found = dict(re.findall(r'<section class="set[^"]*" id="([^"]+)"[^>]*data-tab="([^"]+)"', page))
    want = {sid: tab for tab, ids in TABS.items() for sid in ids}
    assert found == want
    for cat in TABS:
        assert f'class="set-cat" href="#{cat}" data-cat="{cat}"' in page


def test_connections_page_is_now_a_settings_tab(app_client):
    client, _ = app_client()
    login(client)
    r = client.get("/v3/connections", follow_redirects=False)
    assert r.headers["location"] == "/settings#connections"
    page = client.get("/settings").text
    status = page.split('id="connection-status"', 1)[1].split("</section>", 1)[0]
    assert "conn-card" in status or "Nothing is connected" in status


def test_research_sources_are_linked_from_memory(app_client):
    """Review: Research sources could only be found with Ctrl+K."""
    client, _ = app_client()
    login(client)
    tabs = client.get("/v3/memory").text.split('<div class="tabs" role="tablist">', 1)[1].split("</div>", 1)[0]
    assert 'href="/v3/research?mode=research"' in tabs and "Research sources" in tabs


def test_a_message_queued_while_busy_also_gets_research():
    """Review: the queue (Enter while a reply runs) stored the raw text, so the Research button skipped it."""
    from pathlib import Path

    js = (Path(__file__).resolve().parent.parent / "prometheus/web/static/v3/app.js").read_text(encoding="utf-8")
    assert "queued = { text: withResearch(input.value)" in js
