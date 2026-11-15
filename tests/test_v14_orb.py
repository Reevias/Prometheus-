from tests.conftest import login


def test_login_screen_has_the_orb(app_client):
    client, _ = app_client()
    page = client.get("/login").text
    assert "data-orb" in page and "/static/orb.js" in page and 'type=\'password\'' in page
    js = client.get("/static/orb.js")
    assert js.status_code == 200 and "prefers-reduced-motion" in js.text      # still frame for reduced motion
    assert "visibilitychange" in js.text and "IntersectionObserver" in js.text  # pauses when not seen


def test_orb_on_empty_chat_and_status_panel(app_client):
    # v3: the orb is now the sphere. The big one lives on the home screen; a chat shows the mini one in its top bar
    # and the right panel's one, all drawn by /static/v3/sphere.js.
    client, services = app_client()
    login(client)
    assert 'data-sphere="hero"' in client.get("/").text
    cid = services.store.create_chat("New chat")
    page = client.get(f"/c/{cid}").text
    assert 'data-sphere="mini"' in page and 'data-sphere="panel"' in page and "/static/v3/sphere.js" in page
    services.store.add_message(cid, "user", "hi")
    assert 'data-sphere="hero"' not in client.get(f"/c/{cid}").text             # the hero stays on home
    assert client.get("/static/v3/sphere.js").status_code == 200
