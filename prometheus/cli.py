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
    sub.add_parser("shortcut", help="make desktop and Start-menu shortcuts that open this copy")
    args = parser.parse_args(argv)
    return {"init": _init, "serve": _serve, "app": _app, "doctor": _doctor, "update": _update,
            "shortcut": _shortcut}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
