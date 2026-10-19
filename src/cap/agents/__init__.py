"""Thin wrappers around the model-backed agents used in the Create stage. Each builds
the user message from the post record, calls the client, and writes back only the
fields the Prompt Pack says the agent owns. Gate agents live in cap.gates.

Fetched sources are passed as SourceDoc objects; the client wraps them. Version-locked
data (the character block) travels as a static system block, never in the user turn.
"""

from __future__ import annotations

import re

from cap.agents.outputs import (
    P2Call1Output,
    P2Call2Output,
    P3Output,
    P7Output,
    P14Output,
)
from cap.models import ModelClient, ModelResult, SourceDoc
from cap.prompts import spec
from cap.schema.post_record import Claim, FaultType, Platform, PostRecord, Variant

OPENING_LINE_SLOT = "[[HEMANT_OPENING_LINE]]"


class AgentBlockedError(RuntimeError):
    """An agent returned no usable output; the pipeline parks the post."""


def _blocked(name: str, result: ModelResult) -> AgentBlockedError:
    reason = result.output.blocked_reason if result.output else result.stop_reason
    return AgentBlockedError(f"{name}: {reason}")


def draft(
    record: PostRecord,
    client: ModelClient,
    sources: list[SourceDoc],
    character_block: str,
    test_log: str = "",
) -> PostRecord:
    """P2, two calls (D27): call 1 writes the script from the brief, the character block
    and the sources; call 2 lists the claims from the script plus the sources and the
    test log."""
    user1 = (
        f"<brief>{record.brief.model_dump_json()}</brief>\n"
        f"<opening_line_slot>{OPENING_LINE_SLOT}</opening_line_slot>"
    )
    r1 = client.run(
        spec("P2", "1"), user1, P2Call1Output, sources=sources, static_blocks=[character_block]
    )
    if r1.output is None or r1.output.status == "blocked" or r1.output.draft is None:
        raise _blocked("P2 call 1", r1)
    d = r1.output.draft
    record.draft.title, record.draft.script = d.title, d.script
    record.draft.shot_list, record.draft.caption = d.shot_list, d.caption

    r2 = client.run(
        spec("P2", "2"),
        f"<script>{record.draft.script}</script>",
        P2Call2Output,
        sources=sources,
        user_tail=f"<test_log>{test_log}</test_log>",
        static_blocks=[character_block],
    )
    if r2.output is None or r2.output.status == "blocked":
        raise _blocked("P2 call 2", r2)
    record.draft.claims = [
        Claim(
            text=c.text,
            source_url=c.source_url,
            source_quote=c.source_quote,
            in_test_log=c.in_test_log,
        )
        for c in r2.output.claims
    ]
    record.log(f"drafted: {len(record.draft.claims)} claims")
    return record


def critique(record: PostRecord, client: ModelClient) -> tuple[PostRecord, bool]:
    """P3, one pass only (PRD agent 5). Returns (record, accepted)."""
    user = (
        f"<title>{record.draft.title}</title>\n<script>{record.draft.script}</script>\n"
        f"<brief>{record.brief.model_dump_json()}</brief>"
    )
    r = client.run(spec("P3"), user, P3Output)
    if r.output is None or r.output.status == "blocked":
        raise _blocked("P3", r)
    accepted = r.output.verdict == "accept"
    record.log(f"critic: {r.output.verdict}, {len(r.output.issues)} issues")
    return record, accepted


def adapt(record: PostRecord, client: ModelClient, platforms: list[Platform]) -> PostRecord:
    """P7: platform variants. Code checks that no new claims were introduced (PRD
    agent 6 diff check) by requiring every numeral in a variant to appear in the
    script or title."""
    user = (
        f"<title>{record.draft.title}</title>\n<script>{record.draft.script}</script>\n"
        f"<caption>{record.draft.caption}</caption>\n"
        f"<platforms>{[p.value for p in platforms]}</platforms>"
    )
    r = client.run(spec("P7"), user, P7Output)
    if r.output is None or r.output.status == "blocked":
        raise _blocked("P7", r)
    script_numerals = set(_numerals(record.draft.script + " " + record.draft.title))
    record.variants = []
    for v in r.output.variants:
        new_numbers = set(_numerals(v.title + " " + v.caption + " " + v.script_cut))
        new_numbers -= script_numerals
        if new_numbers:
            record.log(f"variant {v.platform} dropped: introduced numerals {sorted(new_numbers)}")
            continue
        record.variants.append(
            Variant(
                platform=Platform(v.platform),
                title=v.title,
                caption=v.caption,
                duration_s=v.duration_s,
                script_cut=v.script_cut,
            )
        )
    return record


def write_canary(
    record: PostRecord, client: ModelClient, fault_type: FaultType, base_draft: dict
) -> PostRecord:
    """P14: a seeded-fault post for the queue. Flagged by code here, never by the
    model; the publisher refuses the flag (D23)."""
    user = f"<fault_type>{fault_type.value}</fault_type>\n<base_draft>{base_draft}</base_draft>"
    r = client.run(spec("P14"), user, P14Output)
    if r.output is None or r.output.status == "blocked" or r.output.canary_draft is None:
        raise _blocked("P14", r)
    d = r.output.canary_draft
    record.draft.title, record.draft.script, record.draft.caption = d.title, d.script, d.caption
    record.flags.canary = True
    record.flags.canary_fault_type = fault_type
    record.log(f"canary written: fault={fault_type.value}")
    return record


def _numerals(text: str) -> list[str]:
    return re.findall(r"\d[\d,.]*", text)
