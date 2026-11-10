"""v1.3: polished chat (formatted answers, steady refresh, titles, progress line)."""
from conftest import login

from prometheus.web.render import render_message


def test_render_escapes_html_first():
    out = str(render_message('<script>alert(1)</script> & <b>x</b>'))
    assert "<script>" not in out and "&lt;script&gt;" in out and "<b>x</b>" not in out


def test_render_bold_code_and_lists():
    out = str(render_message("Done. **3 files** moved with `undo`.\n\n- first\n- second\n\n1. one\n2. two"))
    assert "<strong>3 files</strong>" in out and "<code>undo</code>" in out
    assert out.count("<li>") == 4 and "<ul>" in out and "<ol>" in out


def test_render_code_block_keeps_text_literal():
    out = str(render_message("Run this:\n```\npip install **x** <y>\n```\nthen stop"))
    assert "<pre><code>pip install **x** &lt;y&gt;</code></pre>" in out and "<strong>" not in out


def test_render_heading_and_paragraphs():
    out = str(render_message("## Summary\nline one\nline two\n\nnext para"))
    assert '<p class="h">Summary</p>' in out and out.count("<p>") == 2 and "line one<br>line two" in out


def test_render_never_makes_links_or_images():
    out = str(render_message("[click](javascript:alert(1)) ![x](http://e.com/a.png) https://example.com"))
    assert "<a " not in out and "<img" not in out and "href" not in out


def authed(app_client):
    client, services = app_client()
    login(client)
    return client, services


def test_messages_refresh_returns_nothing_when_unchanged(app_client):
    client, services = authed(app_client)
    chat = services.store.create_chat("c")
    services.store.add_message(chat, "user", "hi")
    assert client.get(f"/chats/{chat}/messages?have=1&busy=0").status_code == 204
    assert client.get(f"/chats/{chat}/messages?have=0&busy=0").status_code == 200
    services.running[chat] = True
    assert client.get(f"/chats/{chat}/messages?have=1&busy=0").status_code == 200
    assert client.get(f"/chats/{chat}/messages").status_code == 200


def test_assistant_answer_is_formatted_and_user_text_is_not(app_client):
    client, services = authed(app_client)
    chat = services.store.create_chat("c")
    services.store.add_message(chat, "user", "**not bold** <i>x</i>")
    services.store.add_message(chat, "assistant", "**bold** answer", "openrouter · big/model")
    page = client.get(f"/chats/{chat}/messages").text
    assert "<strong>bold</strong> answer" in page and "<strong>not bold</strong>" not in page
    assert "&lt;i&gt;x&lt;/i&gt;" in page and "openrouter · big/model" in page


def test_working_line_names_the_current_step(app_client):
    client, services = authed(app_client)
    chat = services.store.create_chat("c")
    task = services.store.create_task(chat, "x")
    services.store.set_task_status(task, "running")
    services.store.add_step(task, "file_sorter", "Sort Downloads", {})
    services.running[chat] = True
    page = client.get(f"/chats/{chat}/messages").text
    assert "working" in page and "Sort Downloads" in page


def test_chat_gets_its_title_from_the_first_message(app_client):
    client, services = authed(app_client)
    h = {"X-CSRF-Token": client.cookies.get("csrf_token")}
    services.start_task = lambda chat_id, text: services.store.add_message(chat_id, "user", text)
    chat = services.store.create_chat("New chat")
    client.post(f"/chats/{chat}/send", data={"text": "sort my Downloads folder and summarise any PDFs about taxes"},
                headers=h)
    title = dict(services.store.chats())[chat]
    assert title.startswith("sort my Downloads folder") and len(title) <= 48
    client.post(f"/chats/{chat}/send", data={"text": "something else"}, headers=h)
    assert dict(services.store.chats())[chat] == title


def test_empty_chat_offers_examples_and_page_has_no_downloads(app_client):
    client, services = authed(app_client)
    page = client.get("/").text
    assert page.count('class="chip"') >= 3 and "<textarea" in page
    import re
    assert not re.search(r'(src|href)=["\']https?://', page)
