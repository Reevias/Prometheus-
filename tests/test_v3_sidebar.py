"""The sidebar after the laptop check (20 Nov 2026): chats and projects first, pages under More; the mic button."""
from __future__ import annotations

import re
import time

from conftest import login

from prometheus import projects as pj


def _project(services, name):
    return pj.create(services.store, services.policy, services.home, name)


def _chat(store, title, project=0, when=None):
    cid = store.create_chat(title)
    if when is not None:
        store._write("UPDATE chats SET created = ? WHERE id = ?", (when, cid))
    if project:
        pj.set_chat_project(store, cid, project)
    return cid


# -- Task 1: the lists --------------------------------------------------------------------------------------------------

def test_recents_are_15_newest_outside_projects_pinned_first(app_client):
    from prometheus.web.sidebar import sidebar_data

    _, services = app_client()
    store = services.store
    proj = _project(services, "Physics")
    ids = [_chat(store, f"chat {i}", when=1000 + i) for i in range(20)]
    _chat(store, "in a project", project=proj, when=5000)
    store.add_message(ids[0], "user", "talked today")          # newest activity: chat 0 comes first after pins
    store.kv_set("chat_flags", {"pinned": [ids[3]], "archived": [ids[19]]})
    data = sidebar_data(store)
    titles = [c["title"] for c in data["recents"]]
    assert len(titles) == 15 and titles[0] == "chat 3" and data["recents"][0]["pinned"]
    assert titles[1] == "chat 0" and "in a project" not in titles and "chat 19" not in titles
    assert data["more_recents"] is True


def test_projects_list_their_8_newest_chats_and_skip_the_trash(app_client):
    from prometheus.web.sidebar import sidebar_data

    _, services = app_client()
    store = services.store
    live, gone = _project(services, "Live"), _project(services, "Old")
    for i in range(10):
        _chat(store, f"live {i}", project=live, when=2000 + i)
    _chat(store, "old one", project=gone)
    pj.trash(store, gone)
    data = sidebar_data(store)
    assert [p["name"] for p in data["projects"]] == ["Live"]
    p = data["projects"][0]
    assert [c["title"] for c in p["chats"]] == [f"live {i}" for i in range(9, 1, -1)] and p["more"] is True


def test_empty_project_shows_only_new_chat_here(app_client):
    client, services = app_client()
    login(client)
    pid = _project(services, "Empty")
    html = client.get("/api/v3/sidebar").text
    block = html.split(f'data-project="{pid}"', 1)[1].split("</details>", 1)[0]
    assert f"/api/v3/projects/{pid}/chats/new" in block and 'class="side-chat' not in block


def test_titles_are_text_not_html(app_client):
    client, services = app_client()
    login(client)
    _chat(services.store, "<b>x</b>")
    html = client.get("/api/v3/sidebar").text
    assert "&lt;b&gt;x&lt;/b&gt;" in html and "<b>x</b>" not in html


def test_200_chats_render_15_and_show_all(app_client):
    client, services = app_client()
    login(client)
    for i in range(200):
        _chat(services.store, f"c{i}", when=1000 + i)
    t = time.monotonic()
    html = client.get("/api/v3/sidebar").text
    assert time.monotonic() - t < 2
    assert html.count('class="side-chat') == 15 and 'href="/v3/chats"' in html


def test_the_open_chat_is_marked(app_client):
    client, services = app_client()
    login(client)
    cid = _chat(services.store, "Open me")
    html = client.get(f"/api/v3/sidebar?chat={cid}").text
    assert f'href="/c/{cid}" class="side-chat on"' in html


# -- Task 2: the sidebar itself -------------------------------------------------------------------------------------------

def test_the_sidebar_keeps_every_page_and_shortcut_and_loads_the_lists(app_client):
    import re

    client, _ = app_client()
    login(client)
    page = client.get("/").text
    side = page.split('id="sidebar"', 1)[1].split("</aside>", 1)[0]
    keys = {int(k): h for h, k in re.findall(r'href="([^"]+)"[^>]*data-key="(\d+)"', side)}
    assert keys == {1: "/", 2: "/v3/tasks", 3: "/v3/agents", 4: "/v3/research?mode=study"}   # four pages (20 Nov)
    for control in ('id="side-new-chat"', 'id="side-search"'):
        assert control in side
    assert 'hx-get="/api/v3/sidebar' in side and "visibilityState" in side


# -- Task 3: the microphone button next to Stop all ------------------------------------------------------------------------

def test_mic_button_toggles_voice_and_saves_it(app_client, monkeypatch):
    from prometheus.config import load_settings

    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    calls = []
    monkeypatch.setattr(services.voice, "ready", lambda: True)
    monkeypatch.setattr(services.voice, "start_if_enabled", lambda: calls.append("start"))
    monkeypatch.setattr(services.voice, "stop", lambda: calls.append("stop"))
    services.settings.voice_enabled = False
    assert 'id="mic-toggle"' in client.get("/").text
    assert client.post("/api/v3/voice/toggle", headers=h).json() == {"on": True}
    assert load_settings().voice_enabled is True and calls == ["start"]
    assert client.post("/api/v3/voice/toggle", headers=h).json() == {"on": False}
    assert load_settings().voice_enabled is False and calls == ["start", "stop"]


def test_mic_button_refuses_while_stopped(app_client, monkeypatch):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    monkeypatch.setattr(services.voice, "ready", lambda: True)
    services.settings.voice_enabled = False
    client.post("/kill", headers=h)
    r = client.post("/api/v3/voice/toggle", headers=h)
    assert r.status_code == 409 and r.json()["error"] == "Everything is stopped. Press Resume to use voice."
    assert services.settings.voice_enabled is False


def test_no_mic_button_without_voice(app_client):
    client, services = app_client()
    login(client)
    services.voice = None
    assert 'id="mic-toggle"' not in client.get("/").text



def test_no_mic_button_and_no_download_when_voice_isnt_set_up(app_client, monkeypatch):
    """Review: the button showed on every install, and a click started the voice download with a false "Voice is on"."""
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    started = []
    monkeypatch.setattr(services.voice, "ready", lambda: False)
    monkeypatch.setattr(services.voice, "start_if_enabled", lambda: started.append(1))
    services.settings.voice_enabled = False
    assert 'id="mic-toggle"' not in client.get("/").text
    r = client.post("/api/v3/voice/toggle", headers=h)
    assert r.status_code == 409 and "Settings" in r.json()["error"]
    assert started == [] and services.settings.voice_enabled is False


def test_voice_ready_means_models_and_packages_are_there(app_client, monkeypatch):
    _, services = app_client()
    v = services.voice
    monkeypatch.setattr(v.models, "missing", lambda: [])
    monkeypatch.setattr(v.models, "packages_missing", lambda: [])
    v._ready_at = None
    assert v.ready() is True
    monkeypatch.setattr(v.models, "missing", lambda: ["voice (Kokoro, 120 MB)"])
    v._ready_at = None
    assert v.ready() is False


def test_voice_ready_works_right_after_the_computer_starts(app_client, monkeypatch):
    """GitHub run: in the first minute after boot (the monotonic clock is small), ready() crashed every page."""
    import time as _time

    from prometheus.voice.service import VoiceService

    _, services = app_client()
    fresh = VoiceService(services, services.voice.models.root if hasattr(services.voice.models, "root") else
                         services.home / "voice")
    monkeypatch.setattr(_time, "monotonic", lambda: 5.0)
    assert fresh.ready() in (True, False)


def test_mic_colours_green_on_red_off():
    """Owner (20 Nov): green when it's working, red when it's paused or off."""
    from pathlib import Path

    css = (Path(__file__).resolve().parent.parent / "prometheus/web/static/v3/theme.css").read_text(encoding="utf-8")
    rules = dict(re.findall(r"(\.mic-toggle(?:\.[a-z]+)?) \{[^}]*?color: var\(--(\w+)\)", css))
    assert rules[".mic-toggle"] == "ok" and rules[".mic-toggle.off"] == "bad" and rules[".mic-toggle.failed"] == "bad"
