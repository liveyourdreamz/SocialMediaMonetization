"""`cap serve`: one app per data folder, on the laptop's own Postgres, with the start-up
lines Hemant reads in the console (the build, the model backend, where to sign in and
each person's code)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cap.config import Settings
    from cap.store import PostStore

ALREADY_RUNNING = 3  # cap serve: another window serves this data folder; it was opened


def signin_secrets(env: Path) -> dict[str, str]:
    """Each person's sign-in secret (cap.setup writes them into .env on first run)."""
    from cap import setup

    return dict(setup.signin_secrets(env))


def _open_later(url: str, delay: float = 1.5) -> None:
    import threading
    import webbrowser

    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def serve(args: argparse.Namespace) -> int:
    import os
    import shutil

    from cap.setup import ensure_env

    env_file = Path(".env")
    model = ensure_env(env_file)
    os.environ.setdefault("CAP_DEV_REVIEWER_HEADER", "true")
    if args.demo:
        os.environ.setdefault("CAP_ARABIC_REVIEWERS", '["layla"]')
        os.environ.setdefault("CAP_PILOT_START", "2026-09-08")
    from cap.config import get_settings

    get_settings.cache_clear()
    settings = get_settings()
    if settings.env not in ("local", "test"):
        print("cap serve is for local runs only", file=sys.stderr)
        return 2

    # Before anything reads the records: a second launch must not touch the first
    # window's posts (it used to mark them interrupted, then fail on the port).
    from cap import instance

    data = Path(settings.data_dir)
    lock = instance.InstanceLock(data, args.port)
    try:
        lock.acquire()
    except instance.AlreadyRunningError as e:
        return _already_running(e.port, args.open, "The app is already running in another window.")
    if instance.port_in_use(args.port):
        lock.release()
        return _already_running(
            args.port,
            args.open,
            f"Something is already using port {args.port}: probably the app in another "
            "window (an older build). Close that window to restart it.",
        )
    try:
        if args.reset and data.exists():
            from cap import localpg

            localpg.stop_if_running(data, settings.pg_bin, settings.pg_port)
            for child in data.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                elif child != lock.path:
                    child.unlink()
        return _run_app(args, settings, data, model, env_file)
    finally:
        lock.release()


def _already_running(port: int, open_browser: bool, why: str) -> int:
    url = f"http://127.0.0.1:{port}/"
    print(why)
    if open_browser:
        import webbrowser

        print(f"Opening {url} in your browser instead of starting a second copy.")
        webbrowser.open(url)
    else:
        print(f"It is at {url}; not starting a second copy.")
    return ALREADY_RUNNING


def _run_app(
    args: argparse.Namespace, settings: Settings, data: Path, model: str, env_file: Path
) -> int:
    from cap import localpg
    from cap.store.postgres import PostgresStore

    try:
        with localpg.database_url(
            settings.database_url, data, settings.pg_bin, settings.pg_port
        ) as url:
            store = PostgresStore.open(url)
            try:
                return _serve_store(args, settings, data, model, env_file, store)
            finally:
                store.close()
    except localpg.LocalPostgresError as e:
        print(f"The app could not start its database: {e}", file=sys.stderr)
        return 1


def _console_log() -> None:
    """The app's own lines (a step starting, finishing, retrying, failing) in the
    console window, with the time."""
    import logging

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))
    log = logging.getLogger("cap")
    log.addHandler(handler)
    log.setLevel(logging.INFO)
    log.propagate = False


def _prepare_records(
    args: argparse.Namespace, settings: Settings, data: Path, store: PostStore
) -> None:
    """Old record files come into the database once; a fresh laptop gets the demo posts."""
    from cap.demo.seed import seed_store
    from cap.store import importer

    if importer.needed(data, store):
        n = importer.import_files(data, store)
        print(f"moved {n} posts from the old record files into the database (files kept aside)")
    if store.unreadable:
        print(f"note: {len(store.unreadable)} stored posts cannot be read by this build")
    if not store.count():
        if settings.renderer == "video":
            print("first run: rendering the demo posts (voice + video), about a minute...")
        seed_store(store, demo=args.demo, canary=args.canary or args.demo)
    print(f"database: {store.count()} posts, {len(store.queue())} waiting")


def _print_build(settings: Settings, model: str) -> None:
    from cap.version import build_id

    if settings.model_backend.value != model:
        print(f"note: CAP_MODEL_BACKEND in the environment overrides .env ({model})")
    print(f"build: {build_id()}")
    if settings.model_backend.value == "anthropic":
        print("model backend: anthropic (real drafts and judges)")
    else:
        print("model backend: mock (sample drafts). `uv run cap key` adds your key.")


def _print_sign_in(args: argparse.Namespace, settings: Settings, env_file: Path) -> str:
    """Where to open the app and each person's sign-in code (with the phone addresses
    when it is open on the Wi-Fi). Returns the sign-in address."""
    from cap import lan
    from cap.ui import templates

    local_url = f"http://127.0.0.1:{args.port}/login"
    how = (
        "Google sign-in is on"
        if settings.google_enabled
        else "sign in with your own code (below); the laptop needs it too"
    )
    print(f"open {local_url} and {how}")
    codes = signin_secrets(env_file)
    if args.host == "0.0.0.0":  # noqa: S104 - the phone path is the point of this host
        addresses = lan.lan_ipv4()
        templates.env.globals["phone_urls"] = lan.phone_urls(args.port, addresses)  # Operator
        print(lan.phone_block(args.port, codes, addresses))
    else:
        for person, code in codes.items():
            print(f"  {person}: {code}")
    return local_url


def _serve_store(
    args: argparse.Namespace,
    settings: Settings,
    data: Path,
    model: str,
    env_file: Path,
    store: PostStore,
) -> int:
    import uvicorn

    from cap.server import create_app, stop_workers

    _prepare_records(args, settings, data, store)
    _print_build(settings, model)
    local_url = _print_sign_in(args, settings, env_file)
    if args.open:
        _open_later(local_url)
    try:
        _console_log()
        # no line per page request: the pages refresh every few seconds and buried the
        # worker's own lines (25 Oct); errors still print
        uvicorn.run(create_app(store), host=args.host, port=args.port, access_log=False)
    finally:
        stop_workers()  # before the database closes
    return 0
