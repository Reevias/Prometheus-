"""M2.1: the new interface (look, Home, chat, command bar, right panel). Replaces the first M2 tests, which checked
for made-up values ("System Monitoring 100%", always "online", the name "Saiveer", a Control PC command)."""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import pytest
from conftest import login

from prometheus.services.telemetry import telemetry_service
from prometheus.services.ui_state import COMMANDS, UIStateService

WEB = Path(__file__).resolve().parents[1] / "prometheus" / "web"


# ---- real readings only -------------------------------------------------------------------------------------

def test_telemetry_is_real_or_missing():
    t = telemetry_service.get_telemetry()
    for key in ("cpu", "ram", "gpu", "temp_c", "disk", "net", "uptime_seconds", "platform"):
        assert key in t
    assert t["cpu"] is None or 0 <= t["cpu"] <= 100
    if t["ram"] is not None:
        assert 0 < t["ram"]["total_gb"] and 0 <= t["ram"]["percent"] <= 100
    assert t["gpu"] is None or (t["gpu"]["model"] and 0 <= t["gpu"]["percent"] <= 100)
    assert t["uptime_seconds"] > 0


def test_network_speed_needs_two_readings():
    from prometheus.services.telemetry import SystemTelemetryService
    svc = SystemTelemetryService()
    assert svc.get_network() is None                          # no fake 0 KB/s on the first reading
    time.sleep(0.05)
    net = svc.get_network()
    assert net is None or (net["down_kbps"] >= 0 and net["up_kbps"] >= 0)


def test_idle_state_invents_nothing():
    s = UIStateService()                                       # no services: nothing known
    assert s.get_current_task() is None and s.get_active_agent() is None
    state = s.get_full_state()
    assert state["online"] is False and state["current_task"] is None


def test_online_needs_a_working_ai_and_not_paused(app_client):
    client, services = app_client()
    login(client)
    st = client.get("/api/v3/state").json()
    assert st["online"] is True and st["current_task"] is None and st["active_agent"] is None
    services.settings.paused = True
    st = client.get("/api/v3/state").json()
    assert st["online"] is False and st["status"] == "paused"


def test_commands_are_real_actions():
    names = {c["command"] for c in COMMANDS}
    assert "/pc" not in names                                   # Control PC is M6: hidden, never a dead command
    for c in COMMANDS:
        assert c["kind"] in ("fill", "go", "do") and c["description"]
        if c["kind"] == "go":
            assert c["value"].startswith("/")


# ---- pages ----------------------------------------------------------------------------------------------------

def test_home_has_the_screenshot_layout(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/").text
    for part in ('class="app"', 'class="sidebar"', 'class="topbar', 'id="panel"', 'data-sphere="hero"',
                 'id="command"', 'id="plus"', 'id="send"', 'class="wave"', ">Prometheus<"):
        assert part in page, part
    assert "slash-indicator" not in page                       # the permanent "/" button is gone: + replaces it
    assert "Saiveer" not in page and "System Monitoring" not in page
    assert "Control PC" not in page                            # hidden until M6
    assert "Add your name" in page                             # the owner's own name, asked for


def test_sidebar_links_all_lead_to_real_pages(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/").text
    nav = re.search(r'<nav class="nav".*?</nav>', page, re.S).group(0)
    links = re.findall(r'href="([^"]+)"', nav)
    assert links[0] == "/" and len(links) == len(set(links))   # no two items lead to the same place
    for href in links:
        r = client.get(href)
        assert r.status_code == 200, href
    labels = re.findall(r"<span>([^<]+)</span></a>", nav)
    assert "Projects" not in labels and "Automations" not in labels   # not built yet: hidden, not dead


def test_send_starts_a_chat_and_shows_the_answer(app_client):
    from prometheus.models.fake import FakeProvider
    client, services = app_client(providers=[FakeProvider(['{"steps": [], "answer": "Hi there, owner."}'], name="fake")])
    csrf = login(client)
    r = client.post("/api/v3/send", data={"text": "hello"}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    chat_id = r.json()["chat_id"]
    assert r.json()["url"] == f"/c/{chat_id}"
    for _ in range(100):
        if chat_id not in services.running and len(services.store.messages(chat_id)) >= 2:
            break
        time.sleep(0.05)
    page = client.get(f"/c/{chat_id}").text
    assert "hello" in page and "Hi there, owner." in page
    assert client.get(f"/c/{chat_id}/messages").status_code == 200


def test_empty_message_is_refused(app_client):
    client, _ = app_client()
    csrf = login(client)
    r = client.post("/api/v3/send", data={"text": "  "}, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 400 and "Type something" in r.json()["error"]


def test_attachments_saved_and_limits_enforced(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    r = client.post("/api/v3/send", data={"text": ""}, files=[("files", ("notes.md", b"# hi", "text/markdown"))], headers=h)
    assert r.status_code == 200                                 # files with no words are fine: Prometheus asks
    r = client.post("/api/v3/send", data={"text": "x"}, files=[("files", ("evil.exe", b"MZ", "application/octet-stream"))], headers=h)
    assert r.status_code == 400 and "can't take" in r.json()["error"]
    services.settings.attach_max_files = 1
    many = [("files", (f"a{i}.txt", b"x", "text/plain")) for i in range(2)]
    r = client.post("/api/v3/send", data={"text": "x"}, files=many, headers=h)
    assert r.status_code == 400 and "Up to 1" in r.json()["error"]


def test_zip_is_listed_never_unpacked(tmp_path):
    import io
    import zipfile

    from prometheus import attachments
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("a.py", "print(1)")
        z.writestr("../../evil.txt", "x")
    p = attachments.save(tmp_path, "code.zip", buf.getvalue(), 25)
    text = attachments.request_text("", [p])
    assert "a.py" in text and "not unpacked" in text
    assert not (tmp_path / "evil.txt").exists() and not (tmp_path.parent / "evil.txt").exists()


def test_old_attachments_are_cleaned(tmp_path):
    from prometheus import attachments
    old = attachments.folder(tmp_path) / "2020-01-01"
    old.mkdir(parents=True)
    (old / "x.txt").write_text("x")
    keep = attachments.folder(tmp_path) / time.strftime("%Y-%m-%d")
    keep.mkdir(parents=True)
    assert attachments.clean_old(tmp_path, 7) == 1 and not old.exists() and keep.exists()


def test_stop_ends_only_this_chats_reply(app_client):
    from prometheus.models.fake import FakeProvider
    gate = threading.Event()

    def slow(messages, system):
        gate.wait(5)
        return '{"steps": [], "answer": "late answer"}'

    client, services = app_client(providers=[FakeProvider(slow, name="fake")])
    csrf = login(client)
    chat_id = client.post("/api/v3/send", data={"text": "think hard"}, headers={"X-CSRF-Token": csrf}).json()["chat_id"]
    time.sleep(0.2)
    r = client.post(f"/api/v3/chats/{chat_id}/stop", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["stopping"] is True
    gate.set()
    for _ in range(100):
        if chat_id not in services.running:
            break
        time.sleep(0.05)
    texts = [m.text for m in services.store.messages(chat_id)]
    assert any(t.startswith("Stopped") for t in texts) and "late answer" not in texts
    assert not services.killswitch.is_set and not services.settings.paused     # not Stop all


def test_profile_name_and_notifications(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    assert client.post("/api/v3/profile", data={"name": "Amit <b>"}, headers=h).json()["name"] == "Amit b"
    assert "Amit b" in client.get("/").text
    services.notify("task", "Your summary is ready")
    assert client.get("/api/v3/state").json()["unread"] == 1
    notes = client.get("/api/v3/notifications").json()
    assert notes[0]["text"] == "Your summary is ready"
    client.post("/api/v3/notifications/read", headers=h)
    assert client.get("/api/v3/state").json()["unread"] == 0


def test_window_buttons_refuse_outside_the_desktop_window(app_client):
    client, _ = app_client()
    csrf = login(client)
    r = client.post("/api/v3/window/minimize", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 409


def test_classic_view_still_reachable_until_switch_over(app_client):
    client, _ = app_client()
    login(client)
    assert 'id="messages"' in client.get("/?view=classic").text


# ---- design rules ---------------------------------------------------------------------------------------------

def test_colours_only_in_the_token_file():
    """Every colour is defined once in tokens.css; the rest of the interface uses var(--…)."""
    colour = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\(\s*\d")
    for f in ("static/v3/theme.css", "static/v3/app.js", "static/v3/sphere.js", "static/v3/chat.js"):
        text = (WEB / f).read_text(encoding="utf-8")
        assert not colour.findall(text), f"{f} writes colours directly: {colour.findall(text)[:5]}"
    tokens = (WEB / "static/v3/tokens.css").read_text(encoding="utf-8")
    hexes = {h.upper() for h in re.findall(r"#[0-9a-fA-F]{6}", tokens)}
    assert {"#0B0B10", "#17171F", "#211D1A", "#FF922B", "#FFC06A", "#F4F1ED", "#AAA5A0"} <= hexes
    assert len(hexes) <= 9                                       # the palette plus the two status colours


def test_nothing_downloaded_from_the_internet():
    for f in ("static/v3/theme.css", "static/v3/tokens.css", "static/v3/app.js", "static/v3/sphere.js",
              "templates/v3/layout.html"):
        text = (WEB / f).read_text(encoding="utf-8")
        assert "http://" not in text and "https://" not in text and "@import" not in text, f


def test_reduced_motion_respected():
    css = (WEB / "static/v3/theme.css").read_text(encoding="utf-8")
    js = (WEB / "static/v3/sphere.js").read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in css and "prefers-reduced-motion" in js
    assert "visibilitychange" in js and '"blur"' in js           # the sphere pauses when hidden or unfocused


# ---- chat extras (M2.1) ----------------------------------------------------------------------------------------

def test_renderer_adds_safe_formatting_only():
    from prometheus.web.render import render_message as r
    assert '<pre data-lang="python"><code class="language-python">' in str(r("```python\nx = '<b>'\n```"))
    assert "&lt;b&gt;" in str(r("```python\nx = '<b>'\n```"))
    t = str(r("| a | b |\n|---|---|\n| <img src=x onerror=1> | **y** |"))
    assert "<table>" in t and "<img" not in t and "&lt;img" in t and "<strong>y</strong>" in t
    m = str(r("Energy $E=mc^2$ and \\(a<b\\) but costs $5 and $10"))
    assert m.count('class="math"') == 2 and "a&lt;b" in m and "$5 and $10" in m
    assert '<div class="math block">' in str(r("$$\n\\frac{1}{2}\n$$"))
    d = str(r("```mermaid\ngraph TD; A[<script>]-->B\n```"))
    assert '<pre class="diagram">' in d and "<script>" not in d
    assert "<a " not in str(r("[x](http://e.vil) ![i](http://e.vil/p.png)"))     # still no links or images


def test_language_tag_cannot_inject():
    from prometheus.web.render import render_message as r
    out = str(r('```py" onmouseover="x\nhi\n```'))
    assert '" onmouseover' not in out and "&#34; onmouseover" in out      # not a fence: escaped as text


def test_export_markdown_json_pdf(app_client):
    client, services = app_client()
    login(client)
    cid = services.store.create_chat("Physics: notes")
    services.store.add_message(cid, "user", "What is energy?")
    services.store.add_message(cid, "assistant", "Energy is the ability to do work.")
    md = client.get(f"/api/v3/chats/{cid}/export?fmt=md")
    assert md.status_code == 200 and "ability to do work" in md.text and 'filename="Physics_ notes.md"' in md.headers["content-disposition"]
    js = client.get(f"/api/v3/chats/{cid}/export?fmt=json").json()
    assert js["title"] == "Physics: notes" and [m["role"] for m in js["messages"]] == ["user", "assistant"]
    pdf = client.get(f"/api/v3/chats/{cid}/export?fmt=pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert client.get(f"/api/v3/chats/{cid}/export?fmt=exe").status_code == 400
    assert client.get("/api/v3/chats/9999/export").status_code == 404


def test_delete_one_message(app_client):
    client, services = app_client()
    csrf = login(client)
    cid = services.store.create_chat("c")
    keep = services.store.add_message(cid, "user", "keep me")
    gone = services.store.add_message(cid, "assistant", "delete me")
    h = {"X-CSRF-Token": csrf}
    assert client.post(f"/api/v3/chats/{cid}/messages/{gone}/delete", headers=h).json() == {"deleted": gone}
    assert [m.text for m in services.store.messages(cid)] == ["keep me"]
    assert client.post(f"/api/v3/chats/{cid}/messages/{gone}/delete", headers=h).status_code == 404
    other = services.store.create_chat("other")
    assert client.post(f"/api/v3/chats/{other}/messages/{keep}/delete", headers=h).status_code == 404   # wrong chat


def test_answers_show_their_visible_steps(app_client):
    client, services = app_client()
    login(client)
    st = services.store
    cid = st.create_chat("c")
    st.add_message(cid, "user", "sort my downloads")
    tid = st.create_task(cid, "sort my downloads")
    sid = st.add_step(tid, "file_sorter", "Sort Downloads", {"folder": "Downloads"})
    st.finish_step(sid, "done", "Moved 12 files")
    st.add_message(cid, "assistant", "Done: 12 files sorted.")
    page = client.get(f"/c/{cid}").text
    assert "1 step" in page and "File Sorter" in page and "Moved 12 files" in page and "Downloads" in page
    for act in ('data-act="copy"', 'data-act="edit"', 'data-act="regenerate"', 'data-act="delete"',
                'id="export"', "/static/v3/chat.js"):
        assert act in page, act
    assert 'data-act="copy-md"' not in page                     # one Copy per message (owner, 13 Nov)
    assert page.count('data-act="copy"') == page.count('class="msg ')


def test_vendor_libraries_are_local_and_licensed():
    v = WEB / "static" / "vendor"
    for f in ("highlight.min.js", "mermaid.min.js", "katex/katex.min.js", "katex/katex.min.css",
              "licenses/highlight.js-LICENSE.txt", "licenses/katex-LICENSE.txt", "licenses/mermaid-LICENSE.txt"):
        assert (v / f).is_file(), f
    js = (WEB / "static/v3/chat.js").read_text(encoding="utf-8")
    assert 'securityLevel: "strict"' in js and "trust: false" in js and "/static/vendor/" in js
    assert "http://" not in js and "https://" not in js


def test_chats_pin_archive_rename_delete(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    st = services.store
    a, b, c = st.create_chat("Alpha"), st.create_chat("Beta"), st.create_chat("Gamma")
    assert client.post(f"/api/v3/chats/{a}/pin", headers=h).json() == {"pinned": True}
    assert client.post(f"/api/v3/chats/{b}/archive", headers=h).json() == {"archived": True}
    page = client.get("/v3/chats").text
    assert page.index("Pinned") < page.index("Alpha") < page.index("Gamma") < page.index("Archived (1)") < page.index("Beta")
    home = client.get("/").text
    assert "Beta" not in home.split('id="v3-data"')[0] and ">Alpha<" in home       # archived: not on Home; pinned first
    assert client.post(f"/api/v3/chats/{b}/archive", headers=h).json() == {"archived": False}
    assert client.post(f"/api/v3/chats/{a}/oops", headers=h).status_code == 404
    client.post(f"/chats/{c}/rename", data={"title": "Physics revision"}, headers=h)
    assert "Physics revision" in client.get("/v3/chats").text
    client.post(f"/chats/{c}/delete", headers=h)
    assert c not in dict(st.chats())


def test_generated_files_strip_and_safe_serving(app_client, tmp_path):
    client, services = app_client()
    login(client)
    st = services.store
    cid = st.create_chat("c")
    tid = st.create_task(cid, "make a chart")
    img = services.home / "Documents" / "chart.png"
    img.parent.mkdir(parents=True, exist_ok=True)
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
    st.add_output(tid, str(img), "created")
    secret = services.home / ".ssh" / "id_rsa"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("PRIVATE")
    st.add_output(tid, str(secret), "found")
    strip = client.get(f"/c/{cid}/files").text
    assert "chart.png" in strip and "out-img" in strip
    ids = {Path(o["path"]).name: o["id"] for o in st.outputs(cid)}
    r = client.get(f"/api/v3/outputs/{ids['chart.png']}/file")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and "inline" in r.headers["content-disposition"]
    d = client.get(f"/api/v3/outputs/{ids['chart.png']}/file?download=1")
    assert "attachment" in d.headers["content-disposition"]
    assert client.get(f"/api/v3/outputs/{ids['id_rsa']}/file").status_code == 403     # .ssh is never served
    assert client.get("/api/v3/outputs/99999/file").status_code == 404


def test_quick_actions_customise(app_client):
    from prometheus.services.ui_state import DEFAULT_QUICK
    client, services = app_client()
    csrf = login(client)
    page = client.get("/").text
    assert ">Code<" in page.replace("</svg>", ">") and 'id="customise"' in page
    mine = [{"label": "Homework", "fill": "Check my homework: ", "icon": "doc"},
            {"label": "<b>bad</b>", "fill": "x", "icon": "javascript:alert(1)"}, {"label": "", "fill": "empty label"}]
    r = client.post("/api/v3/quick-actions", json={"actions": mine}, headers={"X-CSRF-Token": csrf})
    saved = r.json()["actions"]
    assert saved[0] == {"label": "Homework", "fill": "Check my homework: ", "icon": "doc"}
    assert saved[1]["icon"] == "grid" and len(saved) == 2                      # unknown icon replaced, empty label dropped
    page = client.get("/").text
    assert 'data-fill="Check my homework: "' in page and "&lt;b&gt;bad" in page and "<b>bad" not in page
    client.post("/api/v3/quick-actions", json={"actions": []}, headers={"X-CSRF-Token": csrf})
    assert services.settings.quick_actions == [] and DEFAULT_QUICK[0]["label"] in client.get("/").text


def test_settings_in_the_new_look_keep_every_section(app_client):
    import re as _re
    client, _ = app_client()
    login(client)
    new = client.get("/settings").text
    classic = client.get("/settings?view=classic").text
    old_ids = set(_re.findall(r'<section class="set[^"]*" id="([^"]+)"', classic))
    new_ids = set(_re.findall(r'<section class="set[^"]*" id="([^"]+)"', new))
    assert old_ids and old_ids <= new_ids                           # nothing dropped
    assert {"appearance", "about"} <= new_ids
    nav = new.split('class="set-nav"', 1)[1].split("</nav>", 1)[0]
    for sid in new_ids:
        assert f'href="#{sid}"' in nav, sid                          # every section is in the categories menu
    for cat in ("General", "Appearance", "Notifications", "Audio", "AI models", "Integrations", "Privacy", "Security", "About"):
        assert f">{cat}<" in nav
    assert ">Agents<" not in nav and ">Tools<" not in nav            # empty categories aren't shown
    phone = {"user-agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Mobile"}
    assert 'id="v3-settings"' not in client.get("/settings", headers=phone).text


def test_appearance_is_saved_and_applied(app_client):
    client, services = app_client()
    csrf = login(client)
    h = {"X-CSRF-Token": csrf}
    r = client.post("/api/v3/appearance", data={"ui_glow": "9", "ui_sphere": "low", "ui_motion": "reduced",
                                                 "ui_text": "17", "ui_density": "compact"}, headers=h)
    assert r.json() == {"glow": 1.4, "sphere": "low", "motion": "reduced", "text": 17, "density": "compact"}
    page = client.get("/").text
    assert 'data-motion="reduced"' in page and "--glow: 1.4" in page and "font-size: 17px" in page and 'data-density="compact"' in page
    bad = client.post("/api/v3/appearance", data={"ui_sphere": "<x>", "ui_motion": "wild"}, headers=h).json()
    assert bad["sphere"] == "low" and bad["motion"] == "reduced"      # unknown values are ignored
    assert client.post("/api/v3/appearance", data={"ui_text": "big"}, headers=h).status_code == 400


def test_safety_and_account_controls_are_in_the_new_look(app_client):
    client, services = app_client()
    login(client)
    page = client.get("/").text
    assert 'hx-post="/kill"' in page and ">Stop all<" in page
    assert 'hx-post="/logout"' in page and 'href="/approvals"' in page
    services.killswitch.trigger("test")
    page = client.get("/").text
    assert 'hx-post="/kill/reset"' in page and ">Resume<" in page


def test_sidebar_has_no_version_and_no_made_up_badges(app_client):
    client, _ = app_client()
    login(client)
    page = client.get("/").text
    assert "brand-ver" not in page and "v3.0" not in page and 'id="collapse"' in page   # version: Settings, About
    assert "Saiveer" not in page and ">Pro<" not in page and "Control PC" not in page
