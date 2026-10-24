"""The Prompt Pack is the master for prompt text (Pack v1.2 section 20): the prompt
files must be exactly what docs/prompt-pack.md extracts to, every prompt keeps the
five RCICE parts in order, and the P4 and P5 examples are valid output records."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from cap.agents.outputs import P4Item, P5Item

ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "src" / "cap" / "prompts" / "pack"
PARTS = ("role", "context", "instructions", "criteria", "examples")


def test_the_prompt_files_are_what_the_pack_extracts_to(tmp_path):
    subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "extract_prompts.py"),
         str(ROOT / "docs" / "prompt-pack.md"), str(tmp_path)],
        check=True, capture_output=True,
    )  # fmt: skip
    made = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    held = {p.name: p.read_bytes() for p in PACK_DIR.iterdir() if p.is_file()}
    assert sorted(made) == sorted(held)
    drifted = [name for name in made if made[name] != held[name]]
    assert not drifted, f"edit docs/prompt-pack.md and re-extract, not these files: {drifted}"


@pytest.mark.parametrize("path", sorted(p for p in PACK_DIR.glob("P*.xml")), ids=lambda p: p.name)
def test_every_prompt_has_the_five_parts_in_order(path):
    text = path.read_text(encoding="utf-8")
    starts = [text.find(f"<{part}>") for part in PARTS]
    assert all(i >= 0 for i in starts), f"{path.name} is missing a part"
    assert starts == sorted(starts), f"{path.name} parts out of order"
    for part in PARTS:
        assert text.count(f"<{part}>") == 1 and text.count(f"</{part}>") == 1


def _example_outputs(name: str) -> dict[str, dict]:
    text = (PACK_DIR / name).read_text(encoding="utf-8")
    return {
        m.group(1): json.loads(m.group(2))
        for m in re.finditer(r'<example id="([^"]+)">.*?<output>(.*?)</output>', text, re.S)
    }


@pytest.mark.parametrize(("name", "model"), [("P4.xml", P4Item), ("P5.xml", P5Item)])
def test_the_examples_use_only_the_schema_fields(name, model):
    examples = _example_outputs(name)
    assert len(examples) >= 4
    for eid, out in examples.items():
        unknown = set(out) - set(model.model_fields)
        assert not unknown, f"{name} example {eid} uses fields the schema lacks: {unknown}"


@pytest.mark.parametrize(
    ("name", "model", "full"),
    [
        ("P4.xml", P4Item, ["standard", "edge_no_primary_source"]),
        ("P5.xml", P5Item, ["standard", "promotional", "flag_sensitive", "edge_feud", "red"]),
    ],
)
def test_the_full_examples_are_valid_records(name, model, full):
    examples = _example_outputs(name)
    for eid in full:
        model.model_validate(examples[eid])


def test_p5_never_scores_demand_or_money():
    text = (PACK_DIR / "P5.xml").read_text(encoding="utf-8")
    for field in ("demand_fit", "niche_fit", "revenue", "views"):
        assert f'"{field}"' not in text
    assert not {"demand_fit", "niche_fit"} & set(P5Item.model_fields)
