"""The icon by the Windows clock. Closing the window hides it there, so Prometheus keeps listening and reminding;
the icon's menu opens it again or quits. Uses pystray (part of the desktop extra)."""
from __future__ import annotations

import threading


def icon_image(size: int = 64):
    """The Prometheus icon (web/static/icon.png), or a plain ember orb if the file is missing."""
    from pathlib import Path

    from PIL import Image, ImageDraw

    try:
        return Image.open(Path(__file__).resolve().parent / "web" / "static" / "icon.png").convert("RGBA").resize(
            (size, size), Image.LANCZOS)
    except OSError:
        pass

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    for i, color in enumerate([(120, 30, 20), (190, 60, 30), (235, 110, 40), (255, 170, 80)]):
        pad = 4 + i * 7
        d.ellipse([pad, pad, size - pad, size - pad], fill=color + (255,))
    return img


class Tray:
    def __init__(self, show, hide, quit_app, pystray=None):
        self.show, self.hide, self.quit_app = show, hide, quit_app
        self._pystray = pystray
        self.icon = None
        self.hidden = False

    def menu_items(self):
        """(label, action) pairs; the first one is also what a click on the icon does."""
        return [("Open Prometheus", self.open), ("Hide window", self.close_window), ("Quit Prometheus", self.quit)]

    def open(self, *_):
        self.hidden = False
        self.show()

    def close_window(self, *_):
        self.hidden = True
        self.hide()

    def quit(self, *_):
        if self.icon is not None:
            self.icon.stop()
        self.quit_app()

    def start(self) -> bool:
        try:
            pystray = self._pystray or __import__("pystray")
        except Exception:
            return False                       # no tray support: the window just closes as before
        items = [pystray.MenuItem(label, action, default=(i == 0)) for i, (label, action) in
                 enumerate(self.menu_items())]
        self.icon = pystray.Icon("prometheus", icon_image(), "Prometheus", pystray.Menu(*items))
        threading.Thread(target=self.icon.run, name="prometheus-tray", daemon=True).start()
        return True

    def notify(self, text: str) -> None:
        if self.icon is not None:
            try:
                self.icon.notify(text[:250], "Prometheus")
            except Exception:
                pass
