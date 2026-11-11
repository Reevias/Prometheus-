"""Command line: prometheus init | serve | app | doctor | update | shortcut."""
from __future__ import annotations

import argparse
import getpass
import sys
from dataclasses import replace

from prometheus.config import data_dir, load_settings, save_settings

LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")
MIN_PASSWORD = 10


def _init(args) -> int:
    from prometheus.auth import hash_password

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Choose a password for Prometheus: ")
        if getpass.getpass("Type it again: ") != password:
            print("The passwords don't match.")
            return 2
    if len(password) < MIN_PASSWORD:
        print(f"Use at least {MIN_PASSWORD} characters.")
        return 2
    save_settings(replace(load_settings(), password_hash=hash_password(password)))
    print(f"Password saved. Data folder: {data_dir()}")
    print("Start Prometheus with:  prometheus serve")
    return 0


def _serve(args) -> int:
    import uvicorn

    from prometheus.app import build_services
    from prometheus.server import create_app

    settings = load_settings()
    if not settings.password_hash:
        print("No password yet. Run:  prometheus init")
        return 2
    host = args.host or settings.host
    port = args.port or settings.port
    if host not in LOCAL_HOSTS and not settings.allow_lan:
        print(f"Refusing to listen on {host}: other devices could reach Prometheus. "
              "Turn on 'Allow other devices on my Wi-Fi' in Settings first.")
        return 2
    print(f"Prometheus is running at http://{'127.0.0.1' if host in LOCAL_HOSTS else host}:{port}  (Ctrl+C to stop)")
    _note_port(port)
    uvicorn.run(create_app(build_services(settings)), host=host, port=port)
    return 0


def _app(args) -> int:
    settings = load_settings()
    if not settings.password_hash:
        print("No password yet. Run:  prometheus init")
        return 2
    try:
        import webview  # noqa: F401
    except ImportError:
        print('The desktop window needs one extra package. Run:  pip install -e ".[desktop]"')
        print("Until then, use:  prometheus serve")
        return 2
    from prometheus.desktop import run_desktop

    return run_desktop(settings)


def _mcp(args) -> int:
    from prometheus.mcp_server import stdio_bridge

    return stdio_bridge(running_port())


def _note_port(port: int) -> None:
    try:
        (data_dir() / "port").write_text(str(port))
    except OSError:
        pass


def running_port() -> int:
    """The port the running Prometheus listens on (the desktop window may pick a free one)."""
    try:
        return int((data_dir() / "port").read_text().strip())
    except (OSError, ValueError):
        return load_settings().port


def _background(args) -> int:
    """No window: the app on its usual port, listening for "Prometheus" if voice is on. Used when Windows starts."""
    import uvicorn

    from prometheus.app import build_services
    from prometheus.desktop import running_copy
    from prometheus.server import create_app

    settings = load_settings()
    if not settings.password_hash:
        print("No password yet. Run:  prometheus init")
        return 2
    if getattr(args, "after_update", False):           # restarting into a new version: let the old one exit first
        import time

        for _ in range(40):
            if not running_copy(settings.port):
                break
            time.sleep(0.5)
    if running_copy(settings.port):
        print("Prometheus is already running.")
        return 0
    _note_port(settings.port)
    uvicorn.run(create_app(build_services(settings), background=True), host="127.0.0.1", port=settings.port,
                log_level="warning")
    return 0


def _voice_setup(args) -> int:
    from prometheus.config import data_dir as _data
    from prometheus.voice.engines import VoiceModels

    models = VoiceModels(_data() / "voice")
    missing = models.packages_missing()
    if missing:
        print('Voice needs extra packages first. Run:  pip install -e ".[voice]"')
        return 2
    try:
        models.download(progress=print)
    except Exception as e:
        print(f"Download failed: {e}")
        return 1
    settings = load_settings()
    settings.voice_enabled = True
    save_settings(settings)
    print('Voice is on. Say "Prometheus" followed by what you want.')
    return 0


def _autostart(args) -> int:
    from prometheus.launcher import set_autostart

    try:
        print(set_autostart(args.state == "on"))
    except RuntimeError as e:
        print(e)
        return 1
    settings = load_settings()
    settings.voice_autostart = args.state == "on"
    save_settings(settings)
    return 0


def _update(args) -> int:
    from prometheus.launcher import update

    try:
        print(update())
    except RuntimeError as e:
        print(e)
        return 1
    return 0


def _shortcut(args) -> int:
    from prometheus.launcher import make_shortcuts

    try:
        made = make_shortcuts()
    except RuntimeError as e:
        print(e)
        return 1
    for link in made:
        print(f"Shortcut: {link}")
    if not made:
        print("Could not make the shortcuts.")
        return 1
    print("From now on, open Prometheus with the Prometheus shortcut on your desktop or Start menu.")
    return 0


def _doctor(args) -> int:
    from prometheus.memory.store import Store
    from prometheus.models.router import build_router
    from prometheus.safety.budget import Budget
    from prometheus.safety.killswitch import KillSwitch

    from prometheus.launcher import CODE_DIR, old_copy_warning, version

    settings = load_settings()
    print(f"Prometheus:  {version()}  ({CODE_DIR})")
    warning = old_copy_warning()
    if warning:
        print(f"WARNING:     {warning}")
    print(f"Python:      {sys.version.split()[0]}")
    print(f"Data folder: {data_dir()}")
    print(f"Password:    {'set' if settings.password_hash else 'not set (run prometheus init)'}")
    budget = Budget(Store(data_dir() / "memory.db"), settings.budget_usd)
    router = build_router(settings, budget, KillSwitch())
    for name, ok in router.status().items():
        print(f"Model {name:10} {'available' if ok else 'not available'}")
    state = budget.state()
    print(f"Spent:       ${state.spent:.4f} of ${state.cap:.2f}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="prometheus", description="Your personal AI brain.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_init = sub.add_parser("init", help="set the password")
    p_init.add_argument("--password-stdin", action="store_true", help="read the password from standard input")
    p_serve = sub.add_parser("serve", help="start the browser app")
    p_serve.add_argument("--host")
    p_serve.add_argument("--port", type=int)
    sub.add_parser("app", help="open Prometheus in its own desktop window")
    sub.add_parser("doctor", help="check the setup")
    sub.add_parser("update", help="download and install the newest version")
    p_bg = sub.add_parser("background", help="run without a window (listens for its name if voice is on)")
    p_bg.add_argument("--after-update", action="store_true", help=argparse.SUPPRESS)
    sub.add_parser("voice-setup", help="download the voice models and turn voice on")
    p_auto = sub.add_parser("autostart", help="start Prometheus when Windows starts")
    p_auto.add_argument("state", choices=["on", "off"])
    sub.add_parser("shortcut", help="make desktop and Start-menu shortcuts that open this copy")
    sub.add_parser("mcp", help="let another AI app (Antigravity, Claude Desktop) use Prometheus as a tool (stdio)")
    args = parser.parse_args(argv)
    return {"init": _init, "serve": _serve, "app": _app, "doctor": _doctor, "update": _update, "background": _background,
            "voice-setup": _voice_setup, "autostart": _autostart,
            "shortcut": _shortcut, "mcp": _mcp}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
