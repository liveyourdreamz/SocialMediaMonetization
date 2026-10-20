"""`cap` command line: the operator's and the build's entry points.

cap schema export          write src/cap/schema/post_record.schema.json
cap rulebook show          print the judge-facing rulebook text
cap prompts check          verify the extracted prompts match the registry
cap harness run [--runs N] run the regression sets (mock backend unless configured)
cap demo                   run one fake Patch episode through the pipeline (mock)
cap serve [--demo]         run the app locally (Portfolio, queue, card, digest, operator)
                           with demo posts queued; sign in at /login?name=hemant
cap tokens build|check     compile design/proof/tokens.json to the app's tokens.css
cap youtube auth           one-time browser sign-in for YouTube uploads (laptop)
"""

from __future__ import annotations

import argparse
import sys


def main(argv: list[str] | None = None) -> int:
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
    yt = sub.add_parser("youtube")
    yt.add_argument("action", choices=["auth"])
    t = sub.add_parser("tokens")
    t.add_argument("action", choices=["build", "check"])
    args = p.parse_args(argv)

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
    elif args.cmd == "youtube":
        from cap.publisher.youtube import authorise

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
    elif args.cmd == "serve":
        import os
        import shutil
        import threading
        import webbrowser
        from pathlib import Path

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
        import uvicorn

        from cap.approval.app import FileStore, create_app
        from cap.demo.seed import seed_store
        from cap.publisher import LocalFolderAdapter

        data = Path(settings.data_dir)
        if args.reset and data.exists():
            shutil.rmtree(data)
        store = FileStore(data)
        if settings.publisher == "youtube":
            from cap.publisher.youtube import YouTubeAdapter

            store.publisher = YouTubeAdapter()
        else:
            store.publisher = LocalFolderAdapter(data / "published")
        if not store.posts:
            if settings.renderer == "video":
                print("first run: rendering the demo posts (voice + video), about a minute...")
            seed_store(store, demo=args.demo, canary=args.canary or args.demo)
        print(
            f"records in {data.resolve()}: {len(store.posts)} posts, {len(store.queue())} waiting"
        )
        local_url = f"http://127.0.0.1:{args.port}/login"
        if settings.google_enabled:
            how = "Google sign-in is on"
        else:
            how = "sign in as hemant" + (" (or layla for channel C)" if args.demo else "")
        print(f"open {local_url} and {how}")
        if args.host == "0.0.0.0":  # noqa: S104
            print(f"on your phone: http://<this laptop's address>:{args.port}/login")
        if args.open:
            threading.Timer(1.5, lambda: webbrowser.open(local_url)).start()
        uvicorn.run(create_app(store), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
