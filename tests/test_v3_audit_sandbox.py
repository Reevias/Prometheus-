"""Security audit (16 Nov): the code runner. Each test failed before its fix."""
from __future__ import annotations

import sys

import pytest

from prometheus import sandbox


def run(tmp_path, code):
    return sandbox.run_locked(code, sandbox.new_folder(tmp_path))


@pytest.mark.skipif(sys.platform == "win32", reason="the libc route is the Linux/macOS one")
def test_ctypes_cannot_reach_libc_system(tmp_path):
    marker = tmp_path / "pwned.txt"
    r = run(tmp_path, f"import ctypes\nctypes.pythonapi.system(b'echo hi > {marker}')\n")
    assert not marker.exists() and not r.ok


@pytest.mark.skipif(sys.platform == "win32", reason="the libc route is the Linux/macOS one")
def test_ctypes_cannot_reach_other_process_starters(tmp_path):
    for sym in ("popen", "execv", "fork", "posix_spawn", "dlopen", "syscall"):
        r = run(tmp_path, f"import ctypes\ngetattr(ctypes.pythonapi, '{sym}')\nprint('reached')\n")
        assert "reached" not in r.output, sym


def test_ordinary_scripts_with_ctypes_still_work(tmp_path):
    r = run(tmp_path, "import ctypes\nprint(ctypes.sizeof(ctypes.c_int))\n")
    assert r.ok and "4" in r.output


def test_the_approval_shows_the_whole_script():
    """The approval used to cut the script at 1500 characters, so a hidden second half could do anything."""
    import inspect

    from prometheus.agents import code_agent
    src = inspect.getsource(code_agent)
    assert "[:1500]" not in src and "(more)" not in src


def test_mcp_address_must_really_be_local_or_https():
    from prometheus.connectors.mcp_client import HttpServer, MCPError

    for bad in ("http://localhost.evil.com/mcp", "http://127.0.0.1.evil.com/", "http://localhost@evil.com/",
                "http://example.com/mcp", "ftp://x"):
        with pytest.raises(MCPError):
            HttpServer(bad)
    for good in ("https://example.com/mcp", "http://127.0.0.1:8123/mcp", "http://localhost:9/mcp"):
        assert HttpServer(good).url == good
