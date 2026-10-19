"""`cap` command line: the operator's and the build's entry points.

cap schema export          write src/cap/schema/post_record.schema.json
cap rulebook show          print the judge-facing rulebook text
cap prompts check          verify the extracted prompts match the registry
cap harness run [--runs N] run the regression sets (mock backend unless configured)
cap demo                   run one fake Patch episode through the pipeline (mock)
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
        from tests.conftest import SOURCES, happy_fixtures

        from cap.models import MockClient
        from cap.pipeline import PipelineDeps, run_post
        from cap.schema.post_record import Brief, Channel, PostRecord

        rec = PostRecord(
            channel_id=Channel.A,
            brief=Brief(topic="Notely 7-day test", anchor_elements=["test log"]),
        )
        deps = PipelineDeps(
            client=MockClient(happy_fixtures()), anchor_elements={"A": ["test log"]}
        )
        out = run_post(rec, SOURCES, deps)
        print(f"{out.post_id} -> {out.status.value}")
        for line in out.history:
            print("  ", line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
