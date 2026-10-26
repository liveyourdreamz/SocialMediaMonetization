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
    pass

from cap.serve_cmd import ALREADY_RUNNING, serve, signin_secrets  # noqa: F401 - re-exported


def _parser() -> argparse.ArgumentParser:
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
    vi = sub.add_parser("visuals", help="remake a post with real photographs, beside the old one")
    vi.add_argument("action", choices=["trial"])
    vi.add_argument(
        "--post", default=None, help="post id (or its start); default: newest published"
    )
    return p


def _cmd_schema(args: argparse.Namespace) -> int:
    from cap.schema import export_json_schema

    print(f"wrote {export_json_schema()}")
    return 0


def _cmd_rulebook(args: argparse.Namespace) -> int:
    from cap.rulebook import load_rulebook

    print(load_rulebook().as_prompt_text())
    return 0


def _cmd_prompts(args: argparse.Namespace) -> int:
    from cap.prompts import SPECS, new_nonce, pack_version, system_prompt

    n = new_nonce()
    for key, sp in SPECS.items():
        text = system_prompt(sp, n)
        assert "{nonce}" not in text, key
        assert "<role>" in text, key
    print(f"{len(SPECS)} prompt calls load from pack v{pack_version()}")
    return 0


def _cmd_key(args: argparse.Namespace) -> int:
    from cap.setup import check_key, ensure_env, read_key

    ensure_env(Path(".env"), force=True)
    saved = read_key(Path(".env"))
    if saved and not args.no_check:
        print("checking the key with Anthropic...")
        err = check_key(saved)
        print(
            f"key rejected ({err}). Run `uv run cap key` to re-enter it." if err else "key works."
        )
    print("restart the app for the change to take effect.")
    return 0


def _cmd_youtube(args: argparse.Namespace) -> int:
    from cap.publisher.youtube import authorise, test_upload

    if args.action == "test":
        return test_upload()
    print("A browser window opens: sign in to the channel's Google account and allow uploads.")
    print(f"saved: {authorise()}  (git-ignored; set CAP_PUBLISHER=youtube in .env)")
    return 0


def _cmd_tokens(args: argparse.Namespace) -> int:
    from cap.ui.tokens import TOKENS_CSS, build, is_current

    if args.action == "build":
        build()
        print(f"wrote {TOKENS_CSS}")
        return 0
    print(
        "tokens.css is current" if is_current() else "tokens.css is STALE: run `cap tokens build`"
    )
    return 0 if is_current() else 1


def _cmd_harness(args: argparse.Namespace) -> int:
    from cap.harness import run_all
    from cap.models import get_client

    for rep in run_all(get_client(), runs=args.runs):
        print(rep.summary())
    return 0


def _cmd_demo(args: argparse.Namespace) -> int:
    from cap.demo.seed import demo_post

    out = demo_post()
    print(f"{out.post_id} -> {out.status.value}")
    for line in out.history:
        print("  ", line)
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from cap import check

    code = check.run(offline=args.offline)
    if args.full:
        if code:
            print("full run skipped: fix the failed checks first")
            return code
        return check.full_run(channels=args.channels.upper())
    return code


def _cmd_update(args: argparse.Namespace) -> int:
    from cap import updater

    return updater.main(["--dir", args.dir, "--branch", args.branch])


def _cmd_smoke(args: argparse.Namespace) -> int:
    from cap import smoke

    return smoke.run(Path(args.dir).resolve())


def _cmd_serve(args: argparse.Namespace) -> int:
    return serve(args)


def _cmd_db(args: argparse.Namespace) -> int:
    return _db(args.action)


def _cmd_visuals(args: argparse.Namespace) -> int:
    from cap.visuals_trial import trial

    return trial(args.post)


_COMMANDS = {
    "schema": _cmd_schema,
    "rulebook": _cmd_rulebook,
    "prompts": _cmd_prompts,
    "key": _cmd_key,
    "youtube": _cmd_youtube,
    "tokens": _cmd_tokens,
    "harness": _cmd_harness,
    "demo": _cmd_demo,
    "check": _cmd_check,
    "update": _cmd_update,
    "smoke": _cmd_smoke,
    "serve": _cmd_serve,
    "db": _cmd_db,
    "visuals": _cmd_visuals,
}


def main(argv: list[str] | None = None) -> int:
    utf8_console()
    args = _parser().parse_args(argv)
    return _COMMANDS[args.cmd](args)


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


if __name__ == "__main__":
    sys.exit(main())
