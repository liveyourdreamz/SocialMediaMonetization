"""What a render is: the stages, the opening-line slot, the result and the error, and
the script text each stage speaks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from cap.schema.post_record import PostRecord

OPENING_SLOT = "[[HEMANT_OPENING_LINE]]"
OPENING_PLACEHOLDER = "Your opening line goes here"
PLACEHOLDER_UNIT = f"[{OPENING_PLACEHOLDER}]"  # the review render's stand-in paragraph
Stage = Literal["review", "final"]


@dataclass
class RenderResult:
    video: Path
    short: Path | None
    thumbnail: Path
    sha256: str
    duration_s: float
    voice: str


class RenderError(RuntimeError):
    pass


def script_text(record: PostRecord, cut: str | None = None, stage: Stage = "review") -> str:
    """The narration for one stage (review C1). The review render, made before
    approval, marks the opening line's moment with a visible placeholder paragraph;
    the final render, made after approval, speaks the approver's line there (and opens
    with it when a script lost its slot). A final render without a line refuses."""
    text = cut or record.draft.script
    if stage == "review":
        return text.replace(OPENING_SLOT, f"\n\n{PLACEHOLDER_UNIT}\n\n").strip()
    human = record.human_contribution.text.strip()
    if not human:
        raise RenderError("the final render needs the approver's opening line")
    if OPENING_SLOT not in text:
        return f"{human} {text.strip()}"
    return text.replace(OPENING_SLOT, human).strip()


def _clean(p: str) -> str:
    """Strip markdown the drafter may add (headings, emphasis, rules): it would be read
    aloud and shown on a slide otherwise."""
    p = re.sub(r"^\s*(#{1,6}|[-*_]{3,})\s*", "", p, flags=re.M)
    p = re.sub(r"\*{1,2}([^*]+)\*{1,2}", r"\1", p)
    return p.strip()


def paragraphs(text: str) -> list[str]:
    parts = [_clean(p) for p in re.split(r"\n\s*\n", text)]
    parts = [p for p in parts if re.search(r"\w", p)]  # nothing to say or show otherwise
    out: list[str] = []
    for p in parts:  # keep each slide readable: split long paragraphs at sentences
        if len(p) <= 420:
            out.append(p)
            continue
        buf = ""
        for sent in re.split(r"(?<=[.!?؟])\s+", p):
            if len(buf) + len(sent) > 420 and buf:
                out.append(buf.strip())
                buf = ""
            buf += sent + " "
        if buf.strip():
            out.append(buf.strip())
    return out
