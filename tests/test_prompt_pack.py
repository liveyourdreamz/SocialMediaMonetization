"""The Prompt Pack is the master for prompt text (Pack v1.2 section 20, v1.3 section
21): the prompt files must be exactly what docs/prompt-pack.md extracts to, every
prompt keeps the five RCICE parts in order, and every example output is a complete,
valid record of that prompt's schema."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import BaseModel

from cap.agents import outputs as o

ROOT = Path(__file__).resolve().parents[1]
PACK_DIR = ROOT / "src" / "cap" / "prompts" / "pack"
PARTS = ("role", "context", "instructions", "criteria", "examples")
ENVELOPE = {
    "prompt_id": "P0",
    "prompt_version": "test",
    "status": "ok",
    "blocked_reason": None,
    "injection_suspected": False,
    "explanation": "",
}
# prompt file -> schema of one example output (call number -> schema for P2)
SCHEMAS: dict[str, type[BaseModel] | dict[str, type[BaseModel]]] = {
    "P1.xml": o.P1Output,
    "P2.xml": {"1": o.P2Call1Output, "2": o.P2Call2Output},
    "P3.xml": o.P3Output,
    "P4.xml": o.P4Item,
    "P5.xml": o.P5Item,
    "P7.xml": o.P7Output,
    "P8.xml": o.P8Output,
    "P9.xml": o.P9Output,
    "P10_1.xml": o.P10VerifyOutput,
    "P10_2.xml": o.P10VerifyOutput,
    "P14.xml": o.P14Output,
}
THINKING = ("P1.xml", "P9.xml", "P10_1.xml")  # prompts that reason first show the method


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


PROMPTS = sorted(PACK_DIR.glob("P*.xml"))


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.name)
def test_every_prompt_has_the_five_parts_in_order(path):
    text = path.read_text(encoding="utf-8")
    starts = [text.find(f"<{part}>") for part in PARTS]
    assert all(i >= 0 for i in starts), f"{path.name} is missing a part"
    assert starts == sorted(starts), f"{path.name} parts out of order"
    for part in PARTS:
        assert text.count(f"<{part}>") == 1 and text.count(f"</{part}>") == 1


def _examples(path: Path) -> list[tuple[str, str, list[tuple[str, str]]]]:
    """(id, body, [(call, output json)]) for each example."""
    text = path.read_text(encoding="utf-8")
    found = []
    for m in re.finditer(r'<example id="([^"]+)">(.*?)</example>', text, re.S):
        outs = re.findall(r'<output(?: call="(\d)")?>(.*?)</output>', m.group(2), re.S)
        found.append((m.group(1), m.group(2), outs))
    return found


@pytest.mark.parametrize("path", PROMPTS, ids=lambda p: p.name)
def test_three_to_five_examples_none_abbreviated(path):
    examples = _examples(path)
    assert 3 <= len(examples) <= 5, f"{path.name} has {len(examples)} examples"
    for eid, _, outs in examples:
        assert outs, f"{path.name} {eid} has no output"
        for _, raw in outs:
            json.loads(raw)  # every output is a JSON record
            assert "..." not in raw and "…" not in raw, f"{path.name} {eid} is abbreviated"


@pytest.mark.parametrize("name", sorted(SCHEMAS), ids=str)
def test_every_example_is_a_valid_record(name):
    schema = SCHEMAS[name]
    for eid, _, outs in _examples(PACK_DIR / name):
        for call, raw in outs:
            model = schema[call] if isinstance(schema, dict) else schema
            data = json.loads(raw)
            if issubclass(model, o.Envelope):
                data = {**ENVELOPE, **data}
            try:
                model.model_validate(data)
            except Exception as e:  # noqa: BLE001 - name the example in the failure
                pytest.fail(f"{name} example {eid}: {e}")


@pytest.mark.parametrize("name", THINKING)
def test_reasoning_prompts_show_the_method(name):
    for eid, body, _ in _examples(PACK_DIR / name):
        assert "<method>" in body, f"{name} example {eid} has no method line"


def test_p5_never_scores_demand_or_money():
    text = (PACK_DIR / "P5.xml").read_text(encoding="utf-8")
    for field in ("demand_fit", "niche_fit", "revenue", "views"):
        assert f'"{field}"' not in text
    assert not {"demand_fit", "niche_fit"} & set(o.P5Item.model_fields)
