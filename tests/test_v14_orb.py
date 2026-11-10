from tests.conftest import login


def test_login_screen_has_the_orb(app_client):
    client, _ = app_client()
    page = client.get("/login").text
    assert "data-orb" in page and "/static/orb.js" in page and 'type=\'password\'' in page
    js = client.get("/static/orb.js")
    assert js.status_code == 200 and "prefers-reduced-motion" in js.text      # still frame for reduced motion
    assert "visibilitychange" in js.text and "IntersectionObserver" in js.text  # pauses when not seen


def test_orb_on_empty_chat_and_status_panel(app_client):
    client, services = app_client()
    login(client)
    cid = services.store.create_chat("New chat")
    page = client.get(f"/chats/{cid}").text
    assert 'class="orb orb-hero"' in page and 'class="orb orb-side"' in page and "syncOrbs" in page
    services.store.add_message(cid, "user", "hi")
    assert 'orb-hero' not in client.get(f"/chats/{cid}").text                   # hero only when empty
