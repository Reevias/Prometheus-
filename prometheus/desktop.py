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


def running_copy(port: int) -> bool:
    """True when Prometheus already runs on this port (for example started with Windows, listening for its name)."""
    import httpx

    try:
        resp = httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1.5)
        return resp.status_code == 200 and resp.json().get("app") == "prometheus"
    except (httpx.HTTPError, ValueError):
        return False


class AttachedServer:
    """Stands in for LocalServer when the background copy is already running: nothing to start or stop."""

    def __init__(self, port: int):
        self.port = port

    def start(self) -> int:
        return self.port

    def stop(self) -> None:
        pass


def windows_identity(window=None, title: str = "") -> None:
    """On Windows: show Prometheus, not Python, in the taskbar (its own app id and the Prometheus icon)."""
    import sys

    if sys.platform != "win32":
        return
    import ctypes

    from prometheus.launcher import APP_ID, ICON

    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    if window is None:
        return

    def set_icon():
        try:
            user32 = ctypes.windll.user32
            hwnd = user32.FindWindowW(None, title)
            if not hwnd:
                return
            for which, size in ((1, 32), (0, 16)):            # ICON_BIG, ICON_SMALL
                icon = user32.LoadImageW(None, str(ICON), 1, size, size, 0x10)   # IMAGE_ICON, LR_LOADFROMFILE
                if icon:
                    user32.SendMessageW(hwnd, 0x80, which, icon)                # WM_SETICON
        except Exception:
            pass

    try:
        window.events.shown += set_icon
    except Exception:
        pass


def attach_tray(window, settings, pystray=None):
    """Icon by the clock. With it on, closing the window only hides it (voice and reminders keep working)."""
    if not getattr(settings, "tray", True):
        return None
    from prometheus.tray import Tray

    state = {"quitting": False}

    def quit_app():
        state["quitting"] = True
        window.destroy()

    tray = Tray(show=window.show, hide=window.hide, quit_app=quit_app, pystray=pystray)
    if not tray.start():
        return None

    def on_closing():
        if state["quitting"]:
            return True
        tray.close_window()
        tray.notify("Prometheus is still running here. Right-click the icon to quit.")
        return False                            # cancel the close: just hide

    try:
        window.events.closing += on_closing
    except Exception:
        pass
    return tray


def run_desktop(settings: Settings, webview=None, server=None) -> int:
    if webview is None:
        import webview  # pywebview
    if server is None:
        server = AttachedServer(settings.port) if running_copy(settings.port) else LocalServer(settings)
    try:
        port = server.start()
        title = f"Prometheus {version()}"
        windows_identity()                       # before the window exists, so the taskbar groups it as Prometheus
        window = webview.create_window(title, f"http://127.0.0.1:{port}/", width=1220,
                                       height=780, min_size=(860, 560), background_color=BACKGROUND,
                                       text_select=True)  # pywebview blocks selecting and copying text unless asked
        windows_identity(window, title)
        try:
            tray = attach_tray(window, settings) if window is not None else None
        except Exception:                       # the tray is a nicety: never let it stop the window
            tray = None
        webview.start()
        if tray is not None and tray.icon is not None:
            tray.icon.stop()
        return 0
    except Exception as e:
        print(f"The desktop window couldn't open ({e}). Use 'prometheus serve' and your browser instead.")
        return 2
    finally:
        server.stop()
