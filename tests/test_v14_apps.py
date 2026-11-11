import subprocess

import pytest

import prometheus.agents.system as system
from prometheus.agents.opener import Opener, _auto_open
from prometheus.agents.system import SystemAgent
from prometheus.laptop import AppNotInstalled, Laptop, LaptopError, parse_winget_table
from prometheus.sites import search_url, site_for


class FakeLaptop(Laptop):
    def __init__(self, installed=(), packages=()):
        super().__init__(run=self._run, platform="win32")
        self.installed, self.packages, self.calls = {a.lower() for a in installed}, list(packages), []

    def _run(self, argv, timeout=30, env=None):
        self.calls.append((argv, env or {}))
        return 0, ""

    def app_open(self, name):
        if name.lower() not in self.installed:
            raise AppNotInstalled(f"{name} isn't installed on this laptop.")
        return f"opened {name}"

    def find_package(self, name):
        return [p for p in self.packages if name.lower() in p["name"].lower()]


@pytest.fixture
def opened(monkeypatch):
    seen = []
    monkeypatch.setattr(system, "open_site", seen.append)
    return seen


def test_installed_app_opens_without_approval(make_ctx, opened):
    ctx = make_ctx()
    res = SystemAgent(FakeLaptop(installed=["Spotify"])).run("open spotify", {"action": "app_open", "target": "Spotify"}, ctx)
    assert res.status == "done" and res.summary == "opened Spotify" and opened == [] and ctx.store.approvals() == []


def test_missing_app_opens_its_website_without_approval(make_ctx, opened):
    ctx = make_ctx()
    res = SystemAgent(FakeLaptop()).run("open spotify", {"action": "app_open", "target": "Spotify"}, ctx)
    assert res.status == "done" and opened == ["https://open.spotify.com/"] and "isn't installed" in res.summary
    assert ctx.store.approvals() == []                          # nothing to install found: nothing asked
    res = SystemAgent(FakeLaptop()).run("x", {"action": "app_open", "target": "WhatsApp"}, ctx)
    assert opened[-1] == "https://web.whatsapp.com/"


def test_unknown_missing_app_points_to_the_store(make_ctx, opened):
    res = SystemAgent(FakeLaptop()).run("x", {"action": "app_open", "target": "Blender"}, make_ctx())
    assert res.status == "done" and "Microsoft Store" in res.summary and opened == []   # Store search instead


def test_install_by_name_finds_package_then_asks(make_ctx):
    ctx = make_ctx()
    lp = FakeLaptop(packages=[{"name": "Spotify - Music and Podcasts", "id": "9NCBCSZSJRSB", "source": "msstore"}])
    res = SystemAgent(lp).run("install spotify", {"action": "install_app", "target": "Spotify"}, ctx)
    assert res.status == "waiting_approval"
    pending = ctx.store.approvals()[0]
    assert pending.payload == {"action": "install_app", "target": "9NCBCSZSJRSB", "source": "msstore"}
    assert "Spotify - Music and Podcasts" in pending.summary
    system.laptop_action_handler(pending.payload, lp)
    argv = lp.calls[-1][0]
    assert argv[:4] == ["winget", "install", "--id", "9NCBCSZSJRSB"] and argv[-2:] == ["--source", "msstore"]
    assert SystemAgent(FakeLaptop()).run("x", {"action": "install_app", "target": "Nothing"}, ctx).status == "failed"


def test_install_rejects_odd_sources_and_ids():
    lp = FakeLaptop()
    with pytest.raises(LaptopError):
        lp.install_app("9NCBCSZSJRSB", "http://evil")
    with pytest.raises(LaptopError):
        lp.install_app("bad id; calc")


def test_store_opens_with_search_in_env_not_command(make_ctx):
    lp = FakeLaptop()
    res = SystemAgent(lp).run("x", {"action": "store_open", "target": "Spotify"}, make_ctx())
    argv, env = lp.calls[-1]
    assert res.status == "done" and env["PROM_TEXT"] == "Spotify" and "Spotify" not in " ".join(argv)
    assert SystemAgent(lp).run("x", {"action": "store_open", "target": "a;b|c"}, make_ctx()).status == "failed"


def test_real_open_reports_not_installed():
    lp = Laptop(run=lambda argv, timeout=30, env=None: (0, "notfound\n"), platform="win32")
    with pytest.raises(AppNotInstalled):
        lp.app_open("Spotify")
    ok = Laptop(run=lambda argv, timeout=30, env=None: (0, "opened Spotify\n"), platform="win32")
    assert ok.app_open("Spotify") == "opened Spotify"


@pytest.mark.parametrize("name", ["mshta", "wscript", "rundll32", "diskpart", "regedit", "cmd", "powershell"])
def test_script_runners_and_admin_tools_never_open(name):
    lp = Laptop(run=lambda argv, timeout=30, env=None: (0, "opened"), platform="win32")
    with pytest.raises(LaptopError):
        lp.app_open(name)


def test_winget_table_parsing():
    table = """Name                         Id                    Version  Source
---------------------------------------------------------------------
Spotify - Music and Podcasts 9NCBCSZSJRSB          Unknown  msstore
Spotify                      Spotify.Spotify       1.2.50   winget
"""
    rows = parse_winget_table(table)
    assert rows == [{"name": "Spotify - Music and Podcasts", "id": "9NCBCSZSJRSB", "source": "msstore"},
                    {"name": "Spotify", "id": "Spotify.Spotify", "source": "winget"}]
    assert parse_winget_table("No package found matching input criteria.") == []


@pytest.mark.parametrize("url,ok", [
    ("https://open.spotify.com/", True), ("https://open.spotify.com/search/lofi%20beats", True),
    ("https://open.spotify.com/track/4uLU6hMCjMI75M1A2tKUQC", True), ("https://mail.google.com/", True),
    ("https://www.google.com/search?q=weather", True), ("https://en.wikipedia.org/wiki/Prometheus", True),
    ("https://web.whatsapp.com/", True), ("https://www.netflix.com/", True),
    ("https://open.spotify.com/account/subscription", False), ("https://www.amazon.in/gp/buy", False),
    ("https://evil.example.com/", False), ("http://open.spotify.com/", False),
])
def test_which_links_open_without_asking(url, ok):
    assert _auto_open(url) is ok


def test_site_names_and_search_links():
    assert site_for("Spotify") == "https://open.spotify.com/" and site_for("whatsapp web") == "https://web.whatsapp.com/"
    assert site_for("Blender") == ""
    assert search_url("spotify", "lofi beats") == "https://open.spotify.com/search/lofi%20beats"


def test_opener_plays_on_spotify_search(make_ctx):
    seen = []
    ctx = make_ctx()
    res = Opener(launcher=seen.append).run("play lofi on spotify",
                                           {"target": "https://open.spotify.com/search/lofi"}, ctx)
    assert res.status == "done" and seen == ["https://open.spotify.com/search/lofi"] and ctx.store.approvals() == []


SPOTIFY_PKG = [{"name": "Spotify - Music and Podcasts", "id": "9NCBCSZSJRSB", "source": "msstore"}]


class StoreLaptop(FakeLaptop):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.store_searches = []

    def store_open(self, query=""):
        self.store_searches.append(query)
        return "opened store"


def test_missing_app_with_website_opens_site_and_offers_install_in_chat(make_ctx, opened):
    ctx = make_ctx()
    lp = StoreLaptop(packages=SPOTIFY_PKG)
    res = SystemAgent(lp).run("x", {"action": "app_open", "target": "Spotify"}, ctx)
    assert res.status == "done" and opened == ["https://open.spotify.com/"] and "tap Install below" in res.summary
    ask = ctx.store.asks_for_chat(ctx.chat_id())[0]
    assert ask.kind == "install_app_chat" and ask.payload["target"] == "9NCBCSZSJRSB"
    assert ctx.store.big_approvals() == []                    # asked in the chat, not on the Approvals page


def test_missing_app_without_website_asks_to_install(make_ctx, opened):
    ctx = make_ctx()
    lp = StoreLaptop(packages=[{"name": "Blender", "id": "BlenderFoundation.Blender", "source": "winget"}])
    res = SystemAgent(lp).run("x", {"action": "app_open", "target": "Blender"}, ctx)
    assert res.status == "waiting_approval" and opened == [] and "Install below" in res.summary
    assert ctx.store.asks_for_chat(ctx.chat_id())[0].payload["target"] == "BlenderFoundation.Blender"


def test_unknown_app_opens_the_store_search(make_ctx, opened):
    ctx = make_ctx()
    lp = StoreLaptop()
    res = SystemAgent(lp).run("x", {"action": "app_open", "target": "Zorblax"}, ctx)
    assert res.status == "done" and lp.store_searches == ["Zorblax"] and "Microsoft Store" in res.summary
