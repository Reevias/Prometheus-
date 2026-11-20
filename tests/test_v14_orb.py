from tests.conftest import login


def test_login_screen_has_the_new_look(app_client):
    """M2 left the login page in the old 2.x look (style.css + orb.js); it now uses the v3 colours and sphere."""
    client, _ = app_client()
    page = client.get("/login").text
    assert "data-sphere='hero'" in page and "/static/v3/sphere.js" in page and "/static/v3/tokens.css" in page
    assert "type='password'" in page and "#0B0B10" in page
    assert "/static/style.css" not in page and "/static/orb.js" not in page
    assert client.get("/static/style.css").status_code == 404 and client.get("/static/orb.js").status_code == 404
    js = client.get("/static/v3/sphere.js")
    assert js.status_code == 200 and "prefers-reduced-motion" in js.text and "visibilitychange" in js.text
    bad = client.post("/login", data={"password": "wrong"}).text
    assert "Wrong password." in bad and "/static/v3/tokens.css" in bad


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
