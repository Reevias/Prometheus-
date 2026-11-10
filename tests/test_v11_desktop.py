"""v1.1: desktop window launcher and light-on-RAM polling."""
import re

from conftest import login

from prometheus import cli
from prometheus.auth import hash_password
from prometheus.config import Settings, save_settings
from prometheus.desktop import run_desktop


class FakeWebview:
    def __init__(self):
        self.windows = []
        self.started = False

    def create_window(self, title, url, **kw):
        self.windows.append((title, url, kw))

    def start(self, **kw):
        self.started = True


class FakeServer:
    def __init__(self):
        self.started = self.stopped = False

    def start(self):
        self.started = True
        return 54321

    def stop(self):
        self.stopped = True


def test_desktop_opens_local_window_and_stops_server(tmp_home):
    wv, server = FakeWebview(), FakeServer()
    code = run_desktop(Settings(password_hash=hash_password("a long password!")), webview=wv, server=server)
    assert code == 0 and wv.started and server.started and server.stopped
    title, url, kw = wv.windows[0]
    assert title.startswith("Prometheus") and url == "http://127.0.0.1:54321/"
    assert kw["background_color"] == "#0d0709"


def test_desktop_server_stops_even_if_window_crashes(tmp_home):
    class Broken(FakeWebview):
        def start(self, **kw):
            raise RuntimeError("no WebView2")

    server = FakeServer()
    code = run_desktop(Settings(password_hash=hash_password("a long password!")), webview=Broken(), server=server)
    assert code == 2 and server.stopped


def test_cli_app_without_pywebview_explains(tmp_home, monkeypatch, capsys):
    save_settings(Settings(password_hash=hash_password("a long password!")))
    monkeypatch.setitem(__import__("sys").modules, "webview", None)   # import webview -> ImportError
    assert cli.main(["app"]) == 2
    assert "pip install" in capsys.readouterr().out


def test_cli_app_needs_password(tmp_home, capsys):
    assert cli.main(["app"]) == 2
    assert "prometheus init" in capsys.readouterr().out


def test_pages_only_refresh_while_visible(app_client):
    client, services = app_client()
    login(client)
    page = client.get("/").text
    triggers = re.findall(r'hx-trigger="([^"]*every[^"]*)"', page)
    assert triggers and all("visibilityState" in t for t in triggers)


def test_theme_has_no_downloads(app_client):
    client, _ = app_client()
    css = client.get("/static/style.css").text
    assert "@import" not in css and "http://" not in css and "https://" not in css
    assert "--signal" in css and "prefers-reduced-motion" in css
