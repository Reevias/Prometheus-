import os

import pytest

import prometheus.agents.browser_agent as ba
from prometheus.connectors.browser import BrowserError, BrowserSession, kind_of_click

pytest.importorskip("playwright")

PAGE = """<!doctype html><title>Shop</title><body>
<h1>Tiny shop</h1>
<label for=q>Search</label><input id=q name=q>
<label for=pw>Password</label><input id=pw type=password>
<button onclick="document.title='Searched '+document.getElementById('q').value">Go</button>
<button onclick="document.title='Paid'">Place order</button>
<button onclick="document.title='Posted'">Post comment</button>
</body>"""


def test_clicks_are_sorted_by_risk():
    assert kind_of_click("Place order") == "pay" and kind_of_click("Buy now") == "pay"
    assert kind_of_click("Sign in") == "send" and kind_of_click("Post comment") == "send"
    assert kind_of_click("Next page") == "plain" and kind_of_click("Go") == "plain"


@pytest.fixture
def session(tmp_path):
    page = tmp_path / "shop.html"
    page.write_text(PAGE)
    exe = os.environ.get("PROMETHEUS_TEST_CHROMIUM") or ("/opt/pw-browsers/chromium" if os.path.exists(
        "/opt/pw-browsers/chromium") else "")
    s = BrowserSession(tmp_path / "profile", headless=True, channel=None, executable=exe)
    try:
        s.open(page.as_uri())
    except BrowserError as e:
        pytest.skip(f"no browser here: {e}")
    old = ba.SESSION
    ba.SESSION = s
    yield s
    ba.SESSION = old
    s.close()


def test_read_type_click_and_the_rules(session, make_ctx):
    ctx = make_ctx()
    ctx.approvals.register("browser_click", ba.click_handler)
    agent = ba.BrowserAgent()
    seen = agent.run("x", {"action": "read"}, ctx)
    assert "Tiny shop" in seen.summary and "Place order" in seen.summary and "<untrusted" in seen.summary
    assert agent.run("x", {"action": "type", "field": "Search", "text": "pens"}, ctx).status == "done"
    assert agent.run("x", {"action": "click", "label": "Go"}, ctx).status == "done"
    assert session.read()["title"] == "Searched pens"
    # passwords are never typed by Prometheus
    assert agent.run("x", {"action": "type", "field": "Password", "text": "hunter2"}, ctx).status == "failed"
    # paying waits on the Approvals page
    pay = agent.run("x", {"action": "click", "label": "Place order"}, ctx)
    assert pay.status == "waiting_approval" and session.read()["title"] == "Searched pens"
    # posting is asked in the chat, and happens after a yes
    post = agent.run("x", {"action": "click", "label": "Post comment"}, ctx)
    assert post.status == "waiting_approval" and session.read()["title"] == "Searched pens"
    ask = ctx.store.asks_for_chat(ctx.chat_id())[0]
    ctx.approvals.resolve(ask.id, approved=True)
    assert session.read()["title"] == "Posted"


def test_take_over_pauses_prometheus(session, make_ctx):
    session.paused = True
    res = ba.BrowserAgent().run("x", {"action": "read"}, make_ctx())
    assert res.status == "failed" and "Hand back" in res.summary
    session.paused = False
    assert ba.BrowserAgent().run("x", {"action": "read"}, make_ctx()).status == "done"


def test_password_goes_only_through_the_approvals_page(session):
    assert "Signed in" in ba.password_handler({}, "s3cret-pass")
    assert "s3cret-pass" not in str(session.read())


def test_open_refuses_local_addresses(make_ctx, monkeypatch):
    monkeypatch.setattr(ba, "SESSION", object())
    res = ba.BrowserAgent().run("x", {"action": "open", "url": "http://127.0.0.1:8000/settings"}, make_ctx())
    assert res.status == "failed"


def test_partial_labels_and_disguised_buttons_still_count_as_paying(session, make_ctx):
    session.run(lambda page: page.set_content(
        "<form><button>Place order</button></form><button id='checkout-btn'>Continue</button>"
        "<form><input name=q><button type=submit>Go on</button></form>"))
    ctx = make_ctx()
    agent = ba.BrowserAgent()
    assert agent.run("x", {"action": "click", "label": "Place ord"}, ctx).status == "waiting_approval"
    assert agent.run("x", {"action": "click", "label": "Continue"}, ctx).status == "waiting_approval"   # id says checkout
    res = agent.run("x", {"action": "click", "label": "Go on"}, ctx)
    assert res.status == "waiting_approval"                                                       # submits a form
    assert any(a.kind == "browser_click" and "Place order" in a.summary for a in ctx.store.big_approvals())
