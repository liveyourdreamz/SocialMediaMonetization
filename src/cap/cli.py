"""`cap` command line: the operator's and the build's entry points.

cap schema export          write src/cap/schema/post_record.schema.json
cap rulebook show          print the judge-facing rulebook text
cap prompts check          verify the extracted prompts match the registry
cap harness run [--runs N] run the regression sets (mock backend unless configured)
cap demo                   run one fake Patch episode through the pipeline (mock)
cap serve [--port 8000]    run the approval queue locally with the demo post queued
                           (sets CAP_DEV_REVIEWER_HEADER; send X-Reviewer: hemant)
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
    sv.add_argument("--canary", action="store_true", help="queue a canary too (planted fault)")
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
    elif args.cmd == "harness":
        from cap.harness import run_all
        from cap.models import get_client

        for rep in run_all(get_client(), runs=args.runs):
            print(rep.summary())
    elif args.cmd == "demo":
        out = _demo_post()
        print(f"{out.post_id} -> {out.status.value}")
        for line in out.history:
            print("  ", line)
    elif args.cmd == "serve":
        import os

        os.environ.setdefault("CAP_DEV_REVIEWER_HEADER", "true")
        from cap.config import get_settings

        get_settings.cache_clear()
        if get_settings().env not in ("local", "test"):
            print("cap serve is for local runs only", file=sys.stderr)
            return 2
        import uvicorn

        from cap.approval.app import Store, create_app

        store = Store()
        post = _demo_post()
        store.put(post)
        print(f"queued demo post {post.post_id}")
        if args.canary:
            canary = _demo_post(canary=True)
            store.put(canary)
            print(
                f"queued canary {canary.post_id} (planted fault: {canary.flags.canary_fault_type})"
            )
        print(f"open http://127.0.0.1:{args.port}/docs and send header X-Reviewer: hemant")
        uvicorn.run(create_app(store), host="127.0.0.1", port=args.port)
    return 0


def _demo_post(canary: bool = False):  # type: ignore[no-untyped-def]
    from cap.demo import CHARACTER_BLOCK, SOURCES, TEST_LOG, happy_fixtures
    from cap.models import MockClient
    from cap.pipeline import PipelineDeps, run_post
    from cap.schema.post_record import Brief, Channel, FaultType, PlantedFault, PostRecord

    rec = PostRecord(
        channel_id=Channel.A,
        brief=Brief(topic="Notely 7-day test", anchor_elements=["test log"]),
    )
    deps = PipelineDeps(
        client=MockClient(happy_fixtures()),
        anchor_elements={"A": ["test log"]},
        character_blocks={"A": CHARACTER_BLOCK},
        test_logs={rec.post_id: TEST_LOG},
    )
    out = run_post(rec, SOURCES, deps)
    if canary:
        # A hand-planted fault for the local walkthrough; the real canary writer (P14)
        # plants it before the gates. The reviewer must reject with the matching chip.
        out.draft.title = "Why THEIR note apps keep failing (and ours do not)"
        out.flags.canary = True
        out.flags.canary_fault_type = FaultType.OUT_GROUP_TITLE
        out.flags.planted_fault = PlantedFault(
            type=FaultType.OUT_GROUP_TITLE,
            span="THEIR note apps",
            why_catchable="the title sides against an out-group",
        )
    return out


if __name__ == "__main__":
    sys.exit(main())
