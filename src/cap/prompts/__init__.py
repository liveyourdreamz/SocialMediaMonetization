"""Prompt registry.

Prompt text lives in `pack/` (extracted from the Agent Prompt Pack by
scripts/extract_prompts.py); the model and effort for each prompt are set here,
in code, because the Pack's conventions are the decision and the prose that
describes them is not machine-readable. Change this table only with a Pack
version change.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from cap.version import pack_version  # noqa: F401 - re-exported for callers

PACK_DIR = Path(__file__).with_name("pack")

OPUS = "claude-opus-5-5"
SONNET = "claude-sonnet-5-5"
HAIKU = "claude-haiku-5-5"

THINK_LINE = "Think the problem through before you answer."


@dataclass(frozen=True)
class PromptSpec:
    prompt_id: str
    call: str  # "default", "A", "B", "C", "1", "2"
    file: str
    model: str
    effort: str
    closing_line: str | None = None


# Prompt Pack v1.1.1 section 1 conventions, per prompt. No sampling parameters anywhere.
SPECS: dict[tuple[str, str], PromptSpec] = {
    ("P1", "A"): PromptSpec("P1", "A", "P1.xml", OPUS, "high"),
    ("P1", "B"): PromptSpec("P1", "B", "P1.xml", SONNET, "high"),
    ("P2", "1"): PromptSpec("P2", "1", "P2.xml", SONNET, "medium"),
    ("P2", "2"): PromptSpec("P2", "2", "P2.xml", SONNET, "high", THINK_LINE),
    ("P3", "default"): PromptSpec("P3", "default", "P3.xml", SONNET, "high"),
    ("P4", "default"): PromptSpec("P4", "default", "P4.xml", HAIKU, "medium"),
    ("P5", "default"): PromptSpec("P5", "default", "P5.xml", HAIKU, "low"),
    ("P6", "default"): PromptSpec("P6", "default", "P6.xml", HAIKU, "low"),
    ("P7", "default"): PromptSpec("P7", "default", "P7.xml", HAIKU, "low"),
    ("P8", "default"): PromptSpec("P8", "default", "P8.xml", HAIKU, "low"),
    ("P9", "default"): PromptSpec("P9", "default", "P9.xml", HAIKU, "high"),
    ("P10", "A"): PromptSpec("P10", "A", "P10_1.xml", SONNET, "high", THINK_LINE),
    ("P10", "B"): PromptSpec("P10", "B", "P10_1.xml", SONNET, "high", THINK_LINE),
    ("P10", "C"): PromptSpec("P10", "C", "P10_2.xml", HAIKU, "high"),
    ("P11", "default"): PromptSpec("P11", "default", "P11.xml", SONNET, "high"),
    ("P12", "default"): PromptSpec("P12", "default", "P12.xml", HAIKU, "low"),
    ("P13", "default"): PromptSpec("P13", "default", "P13.xml", SONNET, "high"),
    ("P14", "default"): PromptSpec("P14", "default", "P14.xml", SONNET, "medium"),
}

SHARED_ALL = ("shared_context", "untrusted_text_rule", "fail_closed_rule")
# P10 call C is deliberately told nothing about the pipeline (second verifier with its
# own text). It still gets the two security blocks: the untrusted-text rule, because it
# reads a fetched page, and the fail-closed rule.
SHARED_BY_CALL: dict[tuple[str, str], tuple[str, ...]] = {
    ("P10", "C"): ("untrusted_text_rule", "fail_closed_rule"),
}


@lru_cache
def _read(name: str) -> str:
    return (PACK_DIR / name).read_text(encoding="utf-8")


def new_nonce() -> str:
    return secrets.token_hex(8)


def shared_blocks(nonce: str, which: tuple[str, ...] = SHARED_ALL) -> str:
    return "\n\n".join(_read(f"shared_{tag}.xml").strip() for tag in which).replace(
        "{nonce}", nonce
    )


def system_prompt(spec: PromptSpec, nonce: str) -> str:
    """Shared blocks, then the prompt text. The closing line is added by the client
    after the static data blocks so it stays last (Pack section 1)."""
    body = _read(spec.file)
    which = SHARED_BY_CALL.get((spec.prompt_id, spec.call), SHARED_ALL)
    return "\n\n".join([shared_blocks(nonce, which), body])


MAX_UNTRUSTED_CHARS = 200_000  # a fetcher that exceeds this truncates and says so


def untrusted(text: str, nonce: str) -> str:
    """Wrap text that came from outside the pipeline (Prompt Pack section 2).

    The body is JSON-encoded once (non-ASCII preserved, so Arabic sources cost no
    extra tokens), with angle brackets escaped as \\u003c and \\u003e so the encoded
    text can never contain a literal tag; only a tag carrying this run's nonce is a
    real boundary. Callers do not call this directly: the model client does."""
    if not isinstance(text, str):
        raise TypeError("untrusted() takes str only")
    if len(text) > MAX_UNTRUSTED_CHARS:
        text = text[:MAX_UNTRUSTED_CHARS] + " [truncated by the pipeline]"
    encoded = json.dumps(text, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    return f'<untrusted_text id="{nonce}">{encoded}</untrusted_text>'


def spec(prompt_id: str, call: str = "default") -> PromptSpec:
    return SPECS[(prompt_id, call)]
