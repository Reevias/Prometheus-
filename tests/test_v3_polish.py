"""No raw data on screen; a "?" explains hard settings (owner, 20 Nov 2026)."""
from __future__ import annotations

import re

import pytest

from conftest import login


@pytest.mark.parametrize("raw, shown", [
    ("openrouter · nvidia/nemotron-3-ultra-550b-a55b:free", "Answered by Nemotron 3 Ultra 550B A55B (free)"),
    ("coarena · claude-sonnet-4-5", "Answered by Claude Sonnet 4.5"),
    ("ollama · llama3.2:3b", "Answered by Llama3.2 3B (on this laptop)"),
    ("instant", "Answered instantly"),
    ("approval", ""), ("front door", ""), ("", ""),
    ("reminder", "Reminder"),
])
def test_who_answered_is_said_in_words(raw, shown):
    from prometheus.web.polish import who_answered

    assert who_answered(raw) == shown


def test_answers_show_the_friendly_name_and_keep_the_full_one_on_hover(app_client):
    client, services = app_client()
    login(client)
    chat = services.store.create_chat("x")
    services.store.add_message(chat, "user", "hey")
    services.store.add_message(chat, "assistant", "Hi!", "openrouter · nvidia/nemotron-3-ultra-550b-a55b:free")
    page = client.get(f"/c/{chat}/messages").text
    assert "Answered by Nemotron 3 Ultra 550B A55B (free)" in page
    assert 'title="openrouter · nvidia/nemotron-3-ultra-550b-a55b:free"' in page
    assert ">openrouter · nvidia" not in page


def test_steps_show_words_not_json_or_internal_codes(app_client):
    client, services = app_client()
    login(client)
    st = services.store
    chat = st.create_chat("x")
    st.add_message(chat, "user", "summarise it")
    task = st.create_task(chat, "summarise it")
    sid = st.add_step(task, "opener", "Summarise the video", {"kind": "youtube", "summarise": "true"})
    st.finish_step(sid, "waiting_approval", "I'd like to open a link.")
    st.add_message(chat, "assistant", "Done.", "instant")
    page = client.get(f"/c/{chat}/messages").text
    steps = page.split('class="steps-list"', 1)[1].split("</ol>", 1)[0]
    shown = re.sub(r"<[^>]+>", " ", steps)                       # what's on screen (classes and hover text aside)
    assert "waiting_approval" not in shown and "Waiting for your yes" in shown
    assert "{&#34;kind&#34;" not in steps and '{"kind"' not in steps
    assert "Kind" in steps and "youtube" in steps


def test_empty_lists_dont_say_0(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/v3/approvals").text
    assert not re.search(r'Waiting for you <span class="muted">0</span>', page)
    page = client.get("/v3/memory").text
    assert not re.search(r'Memories <span class="muted">0</span>', page)


HARD = ["ai", "voice", "use-prometheus-from-other-ai-apps", "obsidian", "clipboard-and-screenshot-memory",
        "search-inside-files", "watchers", "safety-check", "always-allowed", "places-prometheus-may-use", "github",
        "other-keys", "practice-tests", "features", "study-coach", "focus-mode", "daily-summary"]


def test_hard_settings_have_a_question_mark_that_explains_them(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/settings").text
    for sid in HARD:
        block = page.split(f'id="{sid}"', 1)[1].split("</h3>", 1)[0]
        m = re.search(r'class="help-q"[^>]*data-help="([^"]+)"', block)
        assert m and len(m.group(1)) > 30, sid
    # the old General form was split by the Settings redesign (21 Nov): its "?" marks went with their settings
    moved = "".join(page.split(f'id="{sid}"', 1)[1].split("</section>", 1)[0]
                    for sid in ("spending", "models", "github", "wifi-access"))
    assert moved.count('class="help-q"') >= 5                      # limits, tries, repository, Wi-Fi…


def test_the_voice_fix_is_a_sentence_and_a_copy_button_not_a_raw_command():
    from prometheus.voice import service

    src = open(service.__file__, encoding="utf-8").read()
    assert "irm https://" not in src.split("packages = self.models.packages_missing()", 1)[1].split("return False", 1)[0]
