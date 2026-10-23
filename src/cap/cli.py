"""`cap` command line: the operator's and the build's entry points.

cap schema export          write src/cap/schema/post_record.schema.json
cap rulebook show          print the judge-facing rulebook text
cap prompts check          verify the extracted prompts match the registry
cap harness run [--runs N] run the regression sets (mock backend unless configured)
cap demo                   run one fake Patch episode through the pipeline (mock)
cap smoke                  the whole laptop loop through the real app, no browser (mock)
cap check                  this machine against the real services (key, model, voice, ffmpeg)
cap check --full           ...then one real post per channel, idea to published file
cap serve [--demo]         run the app locally (Portfolio, queue, card, digest, operator)
                           with demo posts queued; asks for the API key on first run
cap key                    enter or change the Anthropic API key (hidden; local .env only)
cap update [--branch B]    bring this folder up to date (best effort; install.bat runs it)
cap tokens build|check     compile design/proof/tokens.json to the app's tokens.css
cap youtube auth           one-time browser sign-in for YouTube uploads (laptop)
cap youtube test           upload a 5-second PRIVATE test clip to the connected channel
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from cap.console import utf8_console

if TYPE_CHECKING:
    from cap.config import Settings
    from cap.store import PostStore

ALREADY_RUNNING = 3  # cap serve: another window serves this data folder; it was opened


def main(argv: list[str] | None = None) -> int:  # noqa: C901 - M1 step 4 splits it
    utf8_console()
    p = argparse.ArgumentParser(prog="cap")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("schema")
    s.add_argument("action", choices=["export"])
    r = sub.add_parser("rulebook")
    r.add_argument("action", choices=["show"])
    pr = sub.add_parser("prompts")
    pr.add_argument("action", choices=["check"])
    h = sub.add_parser("harness")
    h.add_argument("action", choices=["run"])
    h.add_argument("--runs", type=int, default=1)
    sub.add_parser("demo")
    ck = sub.add_parser("check")
    ck.add_argument("--offline", action="store_true", help="skip checks that need the network")
    ck.add_argument(
        "--full",
        action="store_true",
        help="after the checks, one real post per channel end to end (uses API credit)",
    )
    ck.add_argument("--channels", default="ABC", help="with --full: which channels")
    sm = sub.add_parser("smoke")
    sm.add_argument("--dir", default="cap-data-smoke", help="scratch folder (wiped first)")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument(
        "--host", default="127.0.0.1", help="0.0.0.0 to open it on your phone over Wi-Fi"
    )
    sv.add_argument("--canary", action="store_true", help="queue a canary too (planted fault)")
    sv.add_argument("--open", action="store_true", help="open the browser once the app is up")
    sv.add_argument("--reset", action="store_true", help="delete the local records first")
    sv.add_argument(
        "--demo",
        action="store_true",
        help="three channels, a canary, example analytics, an Arabic reviewer",
    )
    up = sub.add_parser("update")
    up.add_argument("--dir", default=".", help="the app folder (a git checkout)")
    up.add_argument("--branch", default="bootstrap")
    k = sub.add_parser("key")
    k.add_argument("--no-check", action="store_true", help="do not test the key against Anthropic")
    yt = sub.add_parser("youtube")
    yt.add_argument("action", choices=["auth", "test"])
    t = sub.add_parser("tokens")
    t.add_argument("action", choices=["build", "check"])
    db = sub.add_parser("db", help="the laptop's own Postgres")
    db.add_argument("action", choices=["install", "status"])
    args = p.parse_args(argv)

    if args.cmd == "db":
        return _db(args.action)

    if args.cmd == "schema":
        from cap.schema import export_json_schema

        print(f"wrote {export_json_schema()}")
    elif args.cmd == "rulebook":
        from cap.rulebook import load_rulebook

        print(load_rulebook().as_prompt_text())
    elif args.cmd == "prompts":
        from cap.prompts import SPECS, new_nonce, pack_version, system_prompt

        n = new_nonce()
        for key, sp in SPECS.items():
            text = system_prompt(sp, n)
            assert "{nonce}" not in text, key
            assert "<role>" in text, key
        print(f"{len(SPECS)} prompt calls load from pack v{pack_version()}")
    elif args.cmd == "key":
        from cap.setup import check_key, ensure_env, read_key

        ensure_env(Path(".env"), force=True)
        saved = read_key(Path(".env"))
        if saved and not args.no_check:
            print("checking the key with Anthropic...")
            err = check_key(saved)
            print(
                f"key rejected ({err}). Run `uv run cap key` to re-enter it."
                if err
                else "key works."
            )
        print("restart the app for the change to take effect.")
    elif args.cmd == "youtube":
        from cap.publisher.youtube import authorise, test_upload

        if args.action == "test":
            return test_upload()
        print("A browser window opens: sign in to the channel's Google account and allow uploads.")
        print(f"saved: {authorise()}  (git-ignored; set CAP_PUBLISHER=youtube in .env)")
    elif args.cmd == "tokens":
        from cap.ui.tokens import TOKENS_CSS, build, is_current

        if args.action == "build":
            build()
            print(f"wrote {TOKENS_CSS}")
        else:
            print(
                "tokens.css is current"
                if is_current()
                else "tokens.css is STALE: run `cap tokens build`"
            )
            return 0 if is_current() else 1
    elif args.cmd == "harness":
        from cap.harness import run_all
        from cap.models import get_client

        for rep in run_all(get_client(), runs=args.runs):
            print(rep.summary())
    elif args.cmd == "demo":
        from cap.demo.seed import demo_post

        out = demo_post()
        print(f"{out.post_id} -> {out.status.value}")
        for line in out.history:
            print("  ", line)
    elif args.cmd == "check":
        from cap import check

        code = check.run(offline=args.offline)
        if args.full:
            if code:
                print("full run skipped: fix the failed checks first")
                return code
            return check.full_run(channels=args.channels.upper())
        return code
    elif args.cmd == "update":
        from cap import updater

        return updater.main(["--dir", args.dir, "--branch", args.branch])
    elif args.cmd == "smoke":
        from cap import smoke

        return smoke.run(Path(args.dir).resolve())
    elif args.cmd == "serve":
        return _serve(args)
    return 0


def signin_secrets(env: Path) -> dict[str, str]:
    """Each person's sign-in secret (cap.setup writes them into .env on first run)."""
    from cap import setup

    return dict(setup.signin_secrets(env))


def _open_later(url: str, delay: float = 1.5) -> None:
    import threading
    import webbrowser

    threading.Timer(delay, lambda: webbrowser.open(url)).start()


def _db(action: str) -> int:
    """`cap db install`: put the portable PostgreSQL programs in place (Windows; other
    systems use their own Postgres). `cap db status`: where they are and whether the
    database is running."""
    import os

    from cap import localpg, pginstall
    from cap.config import get_settings

    settings = get_settings()
    if action == "install":
        if os.name != "nt":
            found = localpg.find_bin(settings.pg_bin)
            print(f"database programs: {found or 'not found; install PostgreSQL 16'}")
            return 0 if found else 1
        try:
            fresh = pginstall.install()
        except pginstall.InstallError as e:
            print(f"  could not set up the database programs: {e}", file=sys.stderr)
            return 1
        print(
            f"  database programs {'installed' if fresh else 'already in place'} "
            f"(PostgreSQL {pginstall.VERSION})"
        )
        return 0
    found = localpg.find_bin(settings.pg_bin)
    if settings.database_url:
        print("database: the configured CAP_DATABASE_URL")
        return 0
    if found is None:
        print("database programs: not found (run install.bat again)")
        return 1
    pg = localpg.LocalPostgres(found, Path(settings.data_dir), settings.pg_port)
    state = (
        "running" if pg.running() else ("set up, stopped" if pg.initialised() else "not set up yet")
    )
    print(f"database programs: {found}\ndatabase: {state} (127.0.0.1:{settings.pg_port})")
    return 0


def _serve(args: argparse.Namespace) -> int:
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


def _serve_store(  # noqa: C901 - M1 step 4 splits it
    args: argparse.Namespace,
    settings: Settings,
    data: Path,
    model: str,
    env_file: Path,
    store: PostStore,
) -> int:
    import uvicorn

    from cap import lan
    from cap.demo.seed import seed_store
    from cap.server import create_app
    from cap.store import importer
    from cap.ui import templates
    from cap.version import build_id

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
    if settings.model_backend.value != model:
        print(f"note: CAP_MODEL_BACKEND in the environment overrides .env ({model})")
    print(f"build: {build_id()}")
    if settings.model_backend.value == "anthropic":
        print("model backend: anthropic (real drafts and judges)")
    else:
        print("model backend: mock (sample drafts). `uv run cap key` adds your key.")
    local_url = f"http://127.0.0.1:{args.port}/login"
    if settings.google_enabled:
        how = "Google sign-in is on"
    else:
        how = "sign in with your own code (below); the laptop needs it too"
    print(f"open {local_url} and {how}")
    codes = signin_secrets(env_file)
    if args.host != "0.0.0.0":  # noqa: S104 - the phone block below already shows them
        for person, code in codes.items():
            print(f"  {person}: {code}")
    if args.host == "0.0.0.0":  # noqa: S104 - the phone path is the point of this host
        addresses = lan.lan_ipv4()
        templates.env.globals["phone_urls"] = lan.phone_urls(args.port, addresses)  # Operator
        print(lan.phone_block(args.port, codes, addresses))
    if args.open:
        _open_later(local_url)
    from cap.server import stop_workers

    try:
        uvicorn.run(create_app(store), host=args.host, port=args.port)
    finally:
        stop_workers()  # before the database closes
    return 0


if __name__ == "__main__":
    sys.exit(main())
