"""The contract between the prompt pack and the character blocks.

22 Oct: a real Falak brief stopped at the critic (P3) because P3 measures pacing "at
the rate in the character block" and Falak's block gave no rate. The mock model never
reads the block, so no test saw it. This file reads every sentence in the pack that
asks the character block for something, and requires each one to map to a rule below
that every block satisfies. A new prompt sentence with no rule fails here, so the gap
cannot come back silently.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cap.characters import ANCHOR_ELEMENTS, FALAK_AR, FALAK_EN, PATCH_EN

PACK = Path(__file__).parents[1] / "src" / "cap" / "prompts" / "pack"
BLOCKS = {"A": PATCH_EN, "B": FALAK_EN, "C": FALAK_AR}

# what the prompt asks for (a phrase in its sentence) -> what every block must contain
RULES: dict[str, re.Pattern[str]] = {
    "rate": re.compile(r"\d+\s*(?:words a minute|كلمات? في الدقيقة)"),
    "catchphrase": re.compile(r"(?i)at most one other catchphrase"),
    "anchor": re.compile(r"(?m)^(?:Anchor elements:|عناصر الربط:)"),
    "beats": re.compile(r"(?m)^(?:Signature format|الصيغة المميزة)"),
    "voice": re.compile(r"(?m)^(?:Voice:|الصوت:)"),
    "opening": re.compile(r"(?m)^(?:Opening line:|السطر الافتتاحي:)"),
    "slot": re.compile(r"(?m)^(?:Opening line:|السطر الافتتاحي:)"),
    "missing": re.compile(r"."),  # the "block missing" example needs nothing
}


def _asks() -> list[tuple[str, str]]:
    out = []
    for f in sorted(PACK.glob("*.xml")):
        text = re.sub(r"\s+", " ", f.read_text(encoding="utf-8"))
        for sentence in re.split(r"(?<=[.;:])\s+", text):
            if "character block" in sentence:
                out.append((f.stem, sentence.strip()))
    return out


def _rule_for(sentence: str) -> str | None:
    low = sentence.lower()
    for key in ("rate", "catchphrase", "anchor", "beats", "missing", "slot", "opening"):
        if key in low:
            return key
    if "voice" in low:
        return "voice"
    return None


def test_every_prompt_ask_of_the_character_block_has_a_rule():
    asks = _asks()
    assert len(asks) >= 6, "the pack should reference the character block"
    unmapped = [(p, s) for p, s in asks if _rule_for(s) is None]
    assert not unmapped, f"add a rule for these prompt sentences: {unmapped}"


@pytest.mark.parametrize("channel", sorted(BLOCKS))
@pytest.mark.parametrize("prompt,sentence", _asks())
def test_each_block_gives_what_the_prompt_asks(channel, prompt, sentence):
    rule = _rule_for(sentence)
    assert rule, sentence
    assert RULES[rule].search(BLOCKS[channel]), (
        f"{prompt} asks: {sentence!r}\nbut channel {channel}'s block has no '{rule}'"
    )


@pytest.mark.parametrize("channel", sorted(BLOCKS))
def test_each_block_carries_its_anchor_phrase(channel):
    # the originality gate requires the anchor; the block must teach it
    assert ANCHOR_ELEMENTS[channel][0] in BLOCKS[channel]
