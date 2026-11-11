from tests.conftest import login


def test_feature_pages(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    from prometheus.agents import money_agent, study_agent

    study_agent.STUDY.add("physics", "Unit of force?", "newton")
    page = client.get("/study").text
    assert "physics" in page and "1 card" in page and "Quiz me" in page
    r = client.post("/study/quiz", data={"deck": "physics"}, headers=h, follow_redirects=False)
    assert r.status_code == 303
    chat = int(r.headers["location"].rsplit("/", 1)[1])
    assert "Unit of force?" in services.store.messages(chat)[-1].text
    assert "Saved" in client.post("/study/export", data={"deck": "physics"}, headers=h).text

    assert "Added ₹250" in client.post("/money/add", data={"amount": "250", "what": "swiggy"}, headers=h).text
    money = client.get("/money").text
    assert "Food" in money and "₹250" in money
    assert "should be a number" in client.post("/money/add", data={"amount": "lots", "what": "x"}, headers=h).text

    added = client.post("/plans/add", data={"title": "Physics lab report", "due": "in 10 days"}, headers=h).text
    plans = client.get("/plans").text
    assert "Physics lab report" in plans
    aid = services.store._rows("SELECT id FROM assignments")[0]["id"]
    assert "Marked Physics lab report as done" in client.post("/plans/done", data={"id": aid}, headers=h).text


def test_menu_lists_every_page(app_client):
    client, services = app_client()
    login(client)
    html = client.get("/settings").text
    for href in ("/study", "/money", "/plans", "/approvals", "/memory", "/costs"):
        assert f'href="{href}"' in html
    assert 'class="menu-btn"' in html and 'id="drawer"' in html
