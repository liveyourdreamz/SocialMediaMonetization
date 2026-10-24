"""The Arabic regression sets (R11): well-formed, scored without their held-out
examples, and never leaked into a prompt (a scored case shown to the model would let
the prompt pass its own test)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from cap.harness import DEFAULT_DIR, load_set

PACK = Path(__file__).resolve().parents[1] / "src" / "cap" / "prompts" / "pack"
EXPECTED = {
    "ar-red": {"fail", "escalate"},
    "ar-green": {"pass"},
    "ar-fact": {"supported", "unsupported", "outdated", "misattributed", "endorsement_wording"},
}


def _all(name: str) -> list[dict]:
    return load_set(name, DEFAULT_DIR, scored_only=False)


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_set_is_well_formed(name):
    items = _all(name)
    assert len(items) >= 8
    assert len({i["id"] for i in items}) == len(items)
    for i in items:
        assert i["language"] == "ar" and re.search(r"[؀-ۿ]", i["test_input"])
        assert i["test_input_en"] and i["source_url"].startswith("http")
        assert i["expected"] in EXPECTED[name]
        assert set(i.get("also_acceptable", [])) <= EXPECTED[name]
        assert 40 <= len(i["test_input"].split()) <= 120
        assert i["use"] in ("test", "example")


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_held_out_examples_are_not_scored(name):
    held = {i["id"] for i in _all(name) if i["use"] == "example"}
    assert len(held) == 2
    assert not held & {i["id"] for i in load_set(name)}


def test_no_scored_case_appears_in_a_prompt():
    prompts = " ".join(p.read_text(encoding="utf-8") for p in PACK.glob("P*.xml"))
    for name in EXPECTED:
        for item in load_set(name):  # scored items only
            # any distinctive 6-word run of the Arabic input would be a leak
            words = item["test_input"].split()
            runs = {" ".join(words[k : k + 6]) for k in range(0, max(1, len(words) - 6), 6)}
            leaked = [r for r in runs if len(r) > 25 and r in prompts]
            assert not leaked, f"{item['id']} is scored but appears in a prompt: {leaked[:1]}"


def test_the_red_set_covers_the_core_rules():
    rules = {r for i in _all("ar-red") for r in i["rule_ids"]}
    assert {"R-01", "R-02", "R-09", "R-16"} <= rules


def test_the_files_are_plain_utf8_json():
    for name in EXPECTED:
        raw = (DEFAULT_DIR / f"{name}-set-v1.0.json").read_bytes()
        json.loads(raw.decode("utf-8"))
