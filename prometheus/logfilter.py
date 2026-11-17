"""Keeps the webhook token out of the access log (M3 B3).

uvicorn writes a line like  POST /hooks/<token> HTTP/1.1  for every request. The token is the password for that
address, so the path is rewritten to /hooks/*** before any log handler sees it. It is done when the log record is
made (not as a filter on one logger), so it still works after uvicorn sets up its own logging.
"""
from __future__ import annotations

import logging
import re

_HOOK = re.compile(r"(/hooks/)[^/\s?\"]+")
_installed = False


def redact(path: str) -> str:
    return _HOOK.sub(r"\1***", path)


def install_log_redaction() -> None:
    global _installed
    if _installed:
        return
    previous = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = previous(*args, **kwargs)
        if record.name == "uvicorn.access":
            if isinstance(record.args, tuple):
                record.args = tuple(redact(a) if isinstance(a, str) else a for a in record.args)
            if isinstance(record.msg, str):
                record.msg = redact(record.msg)
        return record

    logging.setLogRecordFactory(factory)
    _installed = True
