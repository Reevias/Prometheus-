"""Web fetch guard: only public http(s) addresses, checked again on every redirect."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx

MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 5


class UnsafeURL(Exception):
    pass


def check_url(url: str) -> str:
    _checked_address(url)
    return url


def _checked_address(url: str) -> tuple[str, str]:
    """Return (host, one checked IP). Every address the name resolves to must be public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise UnsafeURL(f"Only http(s) links are allowed: {url}")
    host = parts.hostname
    if not host or host.lower() in ("localhost", "localhost.localdomain") or host.lower().endswith(".local"):
        raise UnsafeURL(f"Local addresses are not allowed: {url}")
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError as e:
        raise UnsafeURL(f"Can't resolve {host}") from e
    ips = []
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        if not ip.is_global or ip.is_multicast:
            raise UnsafeURL(f"Private or local address is not allowed: {url}")
        ips.append(ip)
    if not ips:
        raise UnsafeURL(f"Can't resolve {host}")
    return host, str(ips[0])


def _pinned(url: str, ip: str) -> str:
    """Same URL, but pointing at the IP that was checked (stops DNS rebinding)."""
    parts = urlsplit(url)
    host_ip = f"[{ip}]" if ":" in ip else ip
    netloc = host_ip + (f":{parts.port}" if parts.port else "")
    return parts._replace(netloc=netloc).geturl()


def _html_to_text(html: str) -> str:
    try:
        import trafilatura

        text = trafilatura.extract(html) or ""
    except Exception:
        text = ""
    if not text:
        import re

        text = re.sub(r"<[^>]+>", " ", html)
    return " ".join(text.split())


def fetch_text(url: str, http: httpx.Client) -> str:
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        host, ip = _checked_address(current)
        parts = urlsplit(current)
        headers = {"Host": host + (f":{parts.port}" if parts.port else "")}
        extensions = {"sni_hostname": host} if parts.scheme == "https" else {}
        with http.stream("GET", _pinned(current, ip), headers=headers, extensions=extensions,
                         follow_redirects=False, timeout=10.0) as resp:
            if resp.status_code in (301, 302, 303, 307, 308) and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                continue
            resp.raise_for_status()
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                size += len(chunk)
                if size >= MAX_BYTES:
                    break
            html = b"".join(chunks)[:MAX_BYTES].decode(resp.encoding or "utf-8", errors="replace")
            return _html_to_text(html)
    raise UnsafeURL(f"Too many redirects: {url}")
