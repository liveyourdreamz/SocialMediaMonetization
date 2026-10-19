"""Regression harness: runs the labelled test sets through the gates and scores them
(PRD section 7, Prompt Pack section 17, Regression Test Sets v1.0).

Sets and what they measure:
* red    (P1): every item must fail or escalate as labelled; recall target 98%+.
* green  (P1): every item must pass; false-block budget 10% (P1 criteria).
* mfk    (P9): band (clear/doubtful/likely) must match.
* fact   (P10): status must match (outdated/unsupported/misattributed/endorsement).
* inject (P4): injection flag must be raised. P4 is not wired yet; the harness
  reports the set as skipped rather than silently passing.

Repeatability (Prompt Pack section 1): the same item run N times must give the same
verdict on 95%+ of items. D25: Red recall is also measured on reworded variants; those
variants are produced in Foundation week 2 and loaded from the same files.

The harness never changes a gate. It is the number Hemant sees before a prompt or
threshold moves.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from cap.gates.disclosure import mfk_band, mfk_score
from cap.gates.ethics import check_ethics
from cap.models import ModelClient
from cap.schema.post_record import Brief, Channel, PostRecord

DEFAULT_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "testsets"


@dataclass
class ItemResult:
    item_id: str
    expected: str
    got: str
    ok: bool
    runs: list[str] = field(default_factory=list)


@dataclass
class SetReport:
    name: str
    results: list[ItemResult]
    skipped: bool = False

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def correct(self) -> int:
        return sum(r.ok for r in self.results)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    @property
    def repeatability(self) -> float:
        multi = [r for r in self.results if len(r.runs) > 1]
        if not multi:
            return 1.0
        stable = sum(1 for r in multi if len(set(r.runs)) == 1)
        return stable / len(multi)

    def summary(self) -> str:
        if self.skipped:
            return f"{self.name}: skipped (gate not wired)"
        return (
            f"{self.name}: {self.correct}/{self.total} correct ({self.accuracy:.0%}), "
            f"repeatability {self.repeatability:.0%}"
        )


def load_set(name: str, directory: Path = DEFAULT_DIR) -> list[dict]:
    data = json.loads((directory / f"{name}-set-v1.0.json").read_text(encoding="utf-8"))
    return data["items"] if isinstance(data, dict) else data


def _record_from_item(item: dict) -> PostRecord:
    """The test input is a prose description of the post (title, script, captions,
    sponsor), written for the judge to read whole; it goes in the script field. The
    caption carries the standard disclosure so the R-07 hard rule does not pre-empt
    the judge on every item."""
    text = item.get("test_input", "")
    r = PostRecord(channel_id=Channel.A, brief=Brief(topic=item.get("case_title", "")))
    r.draft.title = text.split(".")[0][:120]
    r.draft.script = text
    r.draft.caption = "Presented by an AI character."
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
        got_runs = []
        for _ in range(runs):
            gate = check_ethics(_record_from_item(item), client)
            got_runs.append(gate.verdict.value)
        got = Counter(got_runs).most_common(1)[0][0]
        # Red: a fail where escalate was expected still counts (the post never publishes);
        # a pass never does. Green: only a pass counts.
        ok = got == expected if name == "green" else got != "pass"
        results.append(ItemResult(item["id"], expected, got, ok, got_runs))
    return SetReport(name, results)


def run_mfk_set(client: ModelClient, runs: int = 1, directory: Path = DEFAULT_DIR) -> SetReport:
    from cap.gates.disclosure import check_disclosure

    results = []
    for item in load_set("mfk", directory):
        expected = item.get("expected", "")
        got_runs = []
        for _ in range(runs):
            gate = check_disclosure(_record_from_item(item), client)
            got_runs.append(mfk_band(gate.mfk_score))
        got = Counter(got_runs).most_common(1)[0][0]
        results.append(ItemResult(item["id"], expected, got, got == expected, got_runs))
    return SetReport("mfk", results)


def run_all(client: ModelClient, runs: int = 1, directory: Path = DEFAULT_DIR) -> list[SetReport]:
    reports = [
        run_ethics_set("red", client, runs, directory),
        run_ethics_set("green", client, runs, directory),
        run_mfk_set(client, runs, directory),
        SetReport("fact", [], skipped=True),  # needs source bodies fetched; week 2
        SetReport("inject", [], skipped=True),  # P4 not wired; week 2
    ]
    return reports


__all__ = [
    "SetReport",
    "ItemResult",
    "run_all",
    "run_ethics_set",
    "run_mfk_set",
    "load_set",
    "mfk_score",
]
