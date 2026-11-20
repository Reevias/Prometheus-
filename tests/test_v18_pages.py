from tests.conftest import login


def test_feature_pages(app_client):
    client, services = app_client()
    h = {"X-CSRF-Token": login(client)}
    from prometheus.agents import money_agent, study_agent

    study_agent.STUDY.add("physics", "Unit of force?", "newton")
    for old, new in (("/study", "/v3/research?mode=study"), ("/plans", "/v3/research?mode=study"),
                     ("/money", "/v3/research?mode=money")):                  # the old pages point at their new home
        r = client.get(old, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == new, old
    page = client.get("/v3/research?mode=study").text
    assert "physics" in page and "/api/v3/study/quiz" in page
    assert client.post("/api/v3/study/quiz", data={"deck": "physics"}).status_code == 403       # needs CSRF
    r = client.post("/api/v3/study/quiz", data={"deck": "physics"}, headers=h)
    assert r.status_code == 200
    chat = int(r.json()["url"].rsplit("/", 1)[1])
    assert "Unit of force?" in services.store.messages(chat)[-1].text
    assert "Saved" in client.post("/api/v3/study/export", data={"deck": "physics"}, headers=h).json()["message"]

    assert "Added ₹250" in client.post("/api/v3/money/add", data={"text": "250 swiggy"}, headers=h).json()["message"]
    money = client.get("/v3/research?mode=money").text
    assert "Food" in money and "₹250" in money
    assert client.post("/api/v3/money/add", data={"text": "lots"}, headers=h).status_code == 400

    client.post("/api/v3/deadlines/add", data={"text": "Physics lab report due in 10 days"}, headers=h)
    plans = client.get("/v3/research?mode=study").text
    assert "Physics lab report" in plans
    aid = services.store._rows("SELECT id FROM assignments")[0]["id"]
    done = client.post(f"/api/v3/deadlines/{aid}/done", headers=h).json()
    assert "Marked Physics lab report as done" in done["message"]


def test_menu_lists_every_page(app_client):
    client, services = app_client()
    login(client)
    html = client.get("/settings").text                       # the new screen: 9 sidebar links + your menu
    for href in ("/v3/research?mode=study", "/v3/automations", "/v3/approvals", "/v3/memory", "/settings#ai", "/phone", "/v3/system",
                 "/v3/skills", "/v3/tasks", "/v3/agents"):
        assert f'href="{href}"' in html, href
    old = client.get("/settings?view=classic", follow_redirects=False)   # the classic drawer is gone: v3 only
    assert old.status_code == 200 and 'id="drawer"' not in old.text and 'class="sidebar"' in old.text
