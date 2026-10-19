"""Regression harness: runs the labelled test sets through the gates and scores them
(PRD section 7, Prompt Pack section 17, Regression Test Sets v1.0).

Sets and what they measure:
* red    (P1): every item must fail or escalate as labelled; recall target 98%+.
* green  (P1): every item must pass; false-block budget 10% (P1 criteria).
* mfk    (P9): the band (clear/doubtful/likely) must match.
* fact   (P10): status must match; needs fetched source bodies, so skipped until the
  fetcher exists (week 2).
* inject (P4): injection flag must be raised; P4 is not wired yet, reported as skipped.

Per set the report carries three counts, never one percentage that can hide a
non-run: `exact` (the labelled verdict), `non_miss` (Red only: fail where escalate was
labelled still never publishes), `blocked` (a judge or scorer returned no usable
output). A Red or Green percentage is not printed while any item is blocked, because
"blocked -> escalate -> counted as caught" would make a judge-free run look perfect.

Repeatability (Pack section 1): the first run is the verdict; all runs are compared,
and the share of items whose runs agree is reported (target 95%). Rule coverage: for
Red items the labelled rule_ids must be a subset of the gate's rule_ids (target 90%).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from cap.gates.disclosure import check_disclosure
from cap.gates.ethics import check_ethics
from cap.models import ModelClient
from cap.policy import AI_ROLE_DISCLOSURE
from cap.schema.post_record import Brief, Channel, PostRecord

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "testsets"


@dataclass
class ItemResult:
    item_id: str
    expected: str
    got: str
    exact: bool
    non_miss: bool
    blocked: bool
    rules_covered: bool | None = None
    runs: list[str] = field(default_factory=list)


@dataclass
class SetReport:
    name: str
    results: list[ItemResult]
    skipped: bool = False
    skip_reason: str = ""

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def exact(self) -> int:
        return sum(r.exact for r in self.results)

    @property
    def non_miss(self) -> int:
        return sum(r.non_miss for r in self.results)

    @property
    def blocked(self) -> int:
        return sum(r.blocked for r in self.results)

    @property
    def repeatability(self) -> float:
        multi = [r for r in self.results if len(r.runs) > 1]
        if not multi:
            return 1.0
        return sum(1 for r in multi if len(set(r.runs)) == 1) / len(multi)

    @property
    def rule_coverage(self) -> float | None:
        scored = [r for r in self.results if r.rules_covered is not None]
        if not scored:
            return None
        return sum(1 for r in scored if r.rules_covered) / len(scored)

    def summary(self) -> str:
        if self.skipped:
            return f"{self.name}: skipped ({self.skip_reason})"
        parts = [f"{self.name}: exact {self.exact}/{self.total}"]
        if self.name == "red":
            parts.append(f"non-miss {self.non_miss}/{self.total}")
        if self.blocked:
            parts.append(
                f"BLOCKED {self.blocked}/{self.total} (no percentage: judge or scorer did not answer)"
            )
        else:
            parts.append(f"({self.exact / self.total:.0%})" if self.total else "(empty)")
        parts.append(f"repeatability {self.repeatability:.0%}")
        if self.rule_coverage is not None:
            parts.append(f"rule coverage {self.rule_coverage:.0%}")
        return ", ".join(parts)


def load_set(name: str, directory: Path = DEFAULT_DIR) -> list[dict]:
    data = json.loads((directory / f"{name}-set-v1.0.json").read_text(encoding="utf-8"))
    return data["items"] if isinstance(data, dict) else data


def _record_from_item(item: dict) -> PostRecord:
    """The test input is a prose description of the post (title, script, captions,
    sponsor), written for the judge to read whole; it goes in the script field. The
    caption carries the standard disclosure unless the item is a Red case for R-07
    (a Green R-07 item tests that a present disclosure passes)."""
    text = item.get("test_input", "")
    r = PostRecord(channel_id=Channel.A, brief=Brief(topic=item.get("case_title", "")))
    r.draft.title = text.split(". ")[0][:120]
    r.draft.script = text
    tests_r07 = "R-07" in item.get("rule_ids", []) and item.get("expected") != "pass"
    r.draft.caption = "" if tests_r07 else AI_ROLE_DISCLOSURE
    return r


def _expected_verdict(item: dict) -> str:
    v = item.get("expected", "")
    return "fail" if v in ("fail", "block") else v


def run_ethics_set(
    name: str, client: ModelClient, runs: int = 1, directory: Path = DEFAULT_DIR
) -> SetReport:
    results = []
    for item in load_set(name, directory):
        expected = _expected_verdict(item)
        got_runs, blocked, got_rules = [], False, set()
        for _ in range(runs):
            gate = check_ethics(_record_from_item(item), client)
            got_runs.append(gate.verdict.value)
            blocked = blocked or any(j.status != "ok" for j in gate.judges)
            got_rules |= set(gate.rule_ids)
        got = got_runs[0]
        exact = got == expected
        non_miss = got != "pass" if name == "red" else exact
        covered = set(item.get("rule_ids", [])) <= got_rules if name == "red" else None
        results.append(
            ItemResult(item["id"], expected, got, exact, non_miss, blocked, covered, got_runs)
        )
    return SetReport(name, results)


def run_mfk_set(client: ModelClient, runs: int = 1, directory: Path = DEFAULT_DIR) -> SetReport:
    results = []
    for item in load_set("mfk", directory):
        expected = item.get("expected", "")
        got_runs: list[str] = []
        blocked = False
        for _ in range(runs):
            gate = check_disclosure(_record_from_item(item), client)
            got_runs.append(gate.mfk_band)
            blocked = blocked or gate.mfk_band == "unscored"
        got = got_runs[0]
        results.append(
            ItemResult(
                item["id"], expected, got, got == expected, got == expected, blocked, None, got_runs
            )
        )
    return SetReport("mfk", results)


def run_all(client: ModelClient, runs: int = 1, directory: Path = DEFAULT_DIR) -> list[SetReport]:
    return [
        run_ethics_set("red", client, runs, directory),
        run_ethics_set("green", client, runs, directory),
        run_mfk_set(client, runs, directory),
        SetReport("fact", [], skipped=True, skip_reason="needs fetched source bodies; week 2"),
        SetReport("inject", [], skipped=True, skip_reason="P4 not wired; week 2"),
    ]


__all__ = ["SetReport", "ItemResult", "run_all", "run_ethics_set", "run_mfk_set", "load_set"]
