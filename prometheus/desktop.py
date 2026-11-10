"""Desktop window: Prometheus in its own window, using the web engine built into Windows (WebView2)."""
from __future__ import annotations

import socket
import threading
import time

from prometheus.config import Settings
from prometheus.launcher import version

BACKGROUND = "#0d0709"


class LocalServer:
    """Runs the app on 127.0.0.1 on a free port, in a background thread."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._server = None
        self._thread = None

    def start(self) -> int:
        import uvicorn

        from prometheus.app import build_services
        from prometheus.server import create_app

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))          # bound once and handed to the server, so nothing else can take the port
        port = sock.getsockname()[1]
        config = uvicorn.Config(create_app(build_services(self.settings)), host="127.0.0.1", port=port,
                                log_level="warning")
        self._server = uvicorn.Server(config)
        self._thread = threading.Thread(target=self._server.run, kwargs={"sockets": [sock]}, daemon=True)
        self._thread.start()
        deadline = time.time() + 15
        while not self._server.started and time.time() < deadline:
            time.sleep(0.05)
        if not self._server.started:
            raise RuntimeError("Prometheus did not start in time")
        return port

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)


def run_desktop(settings: Settings, webview=None, server=None) -> int:
    if webview is None:
        import webview  # pywebview
    server = server or LocalServer(settings)
    try:
        port = server.start()
        webview.create_window(f"Prometheus {version()}", f"http://127.0.0.1:{port}/", width=1220, height=780,
                              min_size=(860, 560), background_color=BACKGROUND)
        webview.start()
        return 0
    except Exception as e:
        print(f"The desktop window couldn't open ({e}). Use 'prometheus serve' and your browser instead.")
        return 2
    finally:
        server.stop()
