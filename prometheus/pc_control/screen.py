"""Private desktop screenshots via Pillow ImageGrab (M6 6.4a)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import time
from typing import Callable

from PIL import Image, ImageGrab

from prometheus.pc_control.errors import PCControlError

MAX_WIDTH = 1600


@dataclass(frozen=True)
class ScreenshotResult:
    path: Path
    width: int
    height: int
    timestamp: float


def default_grab() -> Image.Image:
    return ImageGrab.grab(all_screens=True)


def capture_screenshot(
    data_dir: Path,
    *,
    grabber: Callable[[], Image.Image] | None = None,
    max_width: int = MAX_WIDTH,
) -> ScreenshotResult:
    """Capture the screen into `<data_dir>/pc_control/screen_<ts>.png`."""
    fn = grabber or default_grab
    try:
        img = fn()
    except Exception as exc:
        raise PCControlError(f"Could not take screenshot: {exc}") from exc
    if img is None:
        raise PCControlError("Screenshot grabber returned nothing.")

    w, h = img.size
    if w > max_width > 0:
        nh = max(1, round(h * (max_width / w)))
        img = img.resize((max_width, nh), Image.Resampling.LANCZOS)
        w, h = img.size

    out_dir = Path(data_dir) / "pc_control"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.time()
    out_path = out_dir / f"screen_{int(ts * 1000)}.png"
    img.save(out_path, format="PNG")
    return ScreenshotResult(path=out_path, width=w, height=h, timestamp=ts)
