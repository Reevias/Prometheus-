"""Desktop window: Prometheus in its own window, using the web engine built into Windows (WebView2)."""
from __future__ import annotations

import socket
import threading
import time

from prometheus.config import Settings
from prometheus.launcher import version

BACKGROUND = "#0B0B10"                   # the palette base, so there is no flash on opening
ACTIVE_WINDOW = None
ACTIVE_TRAY = None                 # set when the tray icon is running (closing then only hides)


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
        try:
            sock.bind(("127.0.0.1", self.settings.port))   # the usual port, so other apps can find Prometheus
        except OSError:
            sock.bind(("127.0.0.1", 0))      # taken: any free one (bound once, so nothing else can take it)
        port = sock.getsockname()[1]
        try:
            from prometheus.config import data_dir

            (data_dir() / "port").write_text(str(port))
        except OSError:
            pass
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


def on_a_screen(x, y, screens) -> bool:
    """True when (x, y) is on one of the screens connected now (a monitor may have been unplugged since)."""
    for s in screens or []:
        sx, sy = getattr(s, "x", 0) or 0, getattr(s, "y", 0) or 0
        w, h = getattr(s, "width", 0) or 0, getattr(s, "height", 0) or 0
        if sx <= x < sx + w and sy <= y < sy + h:
            return True
    return False


def start_position(settings, webview) -> dict:
    """x/y for the new window: where it was last, if that place is on a screen now; otherwise Windows decides."""
    x, y = getattr(settings, "window_x", None), getattr(settings, "window_y", None)
    if x is None or y is None:
        return {}
    try:
        screens = list(getattr(webview, "screens", []) or [])
    except Exception:
        screens = []
    return {"x": int(x), "y": int(y)} if on_a_screen(x + 40, y + 20, screens) else {}


def remember_position(window, settings, save=None, delay: float = 1.0) -> None:
    """Keep where the window is (its monitor and place), saved a moment after it stops moving."""
    import threading as _threading

    if save is None:
        from prometheus.config import save_settings as save
    state = {"timer": None}

    def store(x, y):
        settings.window_x, settings.window_y = int(x), int(y)
        try:
            save(settings)
        except Exception:
            pass

    def moved(x, y):
        if state["timer"] is not None:
            state["timer"].cancel()
        state["timer"] = _threading.Timer(delay, store, args=(x, y))
        state["timer"].daemon = True
        state["timer"].start()

    try:
        window.events.moved += moved
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
                                       text_select=True,  # pywebview blocks selecting and copying text unless asked
                                       frameless=True,    # the app draws its own themed title bar (M2)
                                       easy_drag=False,   # dragging only from the title bar, not from everywhere
                                       fullscreen=True,   # opens fullscreen; F11 / Esc switch (M2)
                                       **start_position(settings, webview))   # on the monitor it was last on
        windows_identity(window, title)
        remember_position(window, settings)
        from prometheus.hotkey import GlobalHotkey

        hotkey = GlobalHotkey()

        def bring_to_front():
            window.show()
            try:
                window.restore()
            except Exception:
                pass

        hotkey.start(bring_to_front)          # Ctrl+Alt+P from any app; off if another app owns it
        global ACTIVE_WINDOW
        ACTIVE_WINDOW = window
        try:
            tray = attach_tray(window, settings) if window is not None else None
            global ACTIVE_TRAY
            ACTIVE_TRAY = tray if tray is not None and getattr(tray, 'icon', None) is not None else None
        except Exception:                       # the tray is a nicety: never let it stop the window
            tray = None
        webview.start()
        hotkey.stop()
        if tray is not None and tray.icon is not None:
            tray.icon.stop()
        return 0
    except Exception as e:
        print(f"The desktop window couldn't open ({e}). Use 'prometheus serve' and your browser instead.")
        return 2
    finally:
        server.stop()
