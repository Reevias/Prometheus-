"""One safe way to name a downloaded file, for any language."""
from __future__ import annotations

import re
from urllib.parse import quote


def attachment(name: str, ext: str) -> str:
    """The Content-Disposition value for "<name>.<ext>": a plain ASCII name for old browsers, plus the real name (Hindi,
    Chinese...) the standard way. A header can only hold Latin-1, so a raw non-Latin name crashed the download."""
    clean = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name or "").strip(" .")[:60] or "download"
    full = f"{clean}.{ext}"
    plain = re.sub(r"[^A-Za-z0-9 ._()-]", "_", full)
    return f"attachment; filename=\"{plain}\"; filename*=UTF-8''{quote(full)}"
