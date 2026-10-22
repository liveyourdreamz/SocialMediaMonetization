"""Thin wrappers around the model-backed agents used in the Create stage. Each builds
the user message from the post record, calls the client, and writes back only the
fields the Prompt Pack says the agent owns. Gate agents live in cap.gates.

Fetched sources are passed as SourceDoc objects; the client wraps them. Version-locked
data (the character block) travels as a static system block, never in the user turn.
"""

from __future__ import annotations

import json
import re

from cap.agents.outputs import (
    P2Call1Output,
    P2Call2Output,
    P3Output,
    P7Output,
    P14Output,
)
from cap.models import ModelClient, ModelResult, SourceDoc
from cap.policy import AI_ROLE_DISCLOSURE_KEY, disclosure_block
from cap.post_context import post_context
from cap.prompts import spec
from cap.schema.post_record import (
    Claim,
    CriticIssue,
    FaultType,
    PlantedFault,
    Platform,
    PostRecord,
    Variant,
)

OPENING_LINE_SLOT = "[[HEMANT_OPENING_LINE]]"


class AgentBlockedError(RuntimeError):
    """An agent returned no usable output; the pipeline parks the post."""


def _blocked(name: str, result: ModelResult) -> AgentBlockedError:
    return AgentBlockedError(f"{name}: {result.reason}")


def draft(
    record: PostRecord,
    client: ModelClient,
    sources: list[SourceDoc],
    character_block: str,
    test_log: str = "",
    critique: str = "",
    _refit: bool = True,
) -> PostRecord:
    """P2, two calls (D27): call 1 writes the script from the brief, the character block,
    the disclosure fields and the sources (plus the critic's notes on a revision); call
    2 lists the claims from the script, the sources and the test log.

    Code checks after call 2 (Pack P2): every numeral in the script appears in some
    claim's text, else the post parks; each source_quote is an exact substring of its
    cited source, else the claim's source_url is nulled for gate 10 to settle; the
    opening-line slot appears exactly once; at most settings.max_claims claims (one
    rewrite to fit, then the post parks with the count)."""
    from cap.config import get_settings

    limit = get_settings().max_claims
    statics = [character_block, disclosure_block()]
    user1 = (
        f"{post_context(record)}\n"
        f"<brief>{record.brief.model_dump_json()}</brief>\n"
        f"<opening_line_slot>{OPENING_LINE_SLOT}</opening_line_slot>\n"
        f"<claim_limit>Make at most {limit} factual claims in the whole script. A claim is "
        "a statement of fact a viewer could check, and every number is one. Explain the "
        "rest with reasoning and analogy, not further facts.</claim_limit>"
    )
    if critique:
        user1 += f"\n<critique>{critique}</critique>\n<previous_draft>{record.draft.script}</previous_draft>"
    r1 = client.run(spec("P2", "1"), user1, P2Call1Output, sources=sources, static_blocks=statics)
    if r1.output is None or r1.output.status == "blocked" or r1.output.draft is None:
        raise _blocked("P2 call 1", r1)
    d = r1.output.draft
    record.draft.title, record.draft.script = d.title, d.script
    record.draft.shot_list, record.draft.caption = d.shot_list, d.caption
    record.draft.on_screen_text, record.draft.thumbnail_text = d.on_screen_text, d.thumbnail_text
    if record.draft.script.count(OPENING_LINE_SLOT) != 1:
        raise AgentBlockedError("P2 call 1: opening-line slot must appear exactly once")

    r2 = client.run(
        spec("P2", "2"),
        f"{post_context(record)}\n<script>{record.draft.script}</script>",
        P2Call2Output,
        sources=sources,
        user_tail=f"<test_log>{test_log}</test_log>\n<claim_limit>List at most {limit} "
        "claims. Where one sentence states related facts, list it as one claim.</claim_limit>",
        static_blocks=statics,
    )
    if r2.output is None or r2.output.status == "blocked":
        raise _blocked("P2 call 2", r2)
    bodies = {src.url: src.body for src in sources}
    claims = []
    for c in r2.output.claims:
        claim = Claim(
            text=c.text,
            source_url=c.source_url,
            source_quote=c.source_quote,
            in_test_log=c.in_test_log,
        )
        if claim.source_url and claim.source_quote:
            body = bodies.get(claim.source_url, test_log if claim.in_test_log else "")
            if " ".join(claim.source_quote.split()) not in " ".join(body.split()):
                claim.source_url = None  # gate 10 settles it
                claim.source_quote = None
        claims.append(claim)
    if len(claims) > limit:
        if not _refit:
            raise AgentBlockedError(
                f"P2: {len(claims)} claims after a rewrite; the limit is {limit} "
                "(CAP_MAX_CLAIMS). Narrow the topic or raise the limit."
            )
        record.log(f"drafted {len(claims)} claims, over the limit of {limit}: rewriting to fit")
        note = (
            f"The script makes {len(claims)} factual claims; the limit is {limit}. Keep the "
            f"{limit} that carry the explanation, cut or merge the rest, and remove any "
            "number that is not among them."
        )
        return draft(
            record,
            client,
            sources,
            character_block,
            test_log,
            critique=(critique + "\n" + note).strip(),
            _refit=False,
        )
    record.draft.claims = claims
    record.draft.source_list = sorted(
        {c.source_url for c in claims if c.source_url and not c.source_url.startswith("testlog://")}
    )
    covered = " ".join(c.text for c in claims)
    # every surface the viewer reads, not just the script (review M4)
    seen = " ".join(
        [
            record.draft.script,
            record.draft.title,
            record.draft.thumbnail_text,
            *record.draft.on_screen_text,
        ]
    )
    missing = sorted(set(_numerals(seen)) - set(_numerals(covered)))
    if missing:
        raise AgentBlockedError(
            f"P2: numerals on screen, in the title or the script with no claim: {missing}"
        )
    record.log(f"drafted: {len(record.draft.claims)} claims")
    return record


def critique(
    record: PostRecord, client: ModelClient, character_block: str = ""
) -> tuple[PostRecord, bool]:
    """P3, one pass only (PRD agent 5). The issues are stored on the record so they
    travel to the gates and are revealed on the card after the verdict (D33)."""
    draft_json = record.draft.model_dump_json(include={"title", "script", "caption", "claims"})
    user = (
        f"{post_context(record)}\n<draft>{draft_json}</draft>\n"
        f"<brief>{record.brief.model_dump_json()}</brief>"
    )
    r = client.run(
        spec("P3"), user, P3Output, static_blocks=[character_block] if character_block else None
    )
    if r.output is None or r.output.status == "blocked":
        raise _blocked("P3", r)
    record.draft.critic_verdict = r.output.verdict
    record.draft.critic_issues = [
        CriticIssue(
            rubric_item=i.rubric_item, severity=i.severity, span=i.span, instruction=i.instruction
        )
        for i in r.output.issues
    ]
    accepted = r.output.verdict == "accept"
    record.log(f"critic: {r.output.verdict}, {len(r.output.issues)} issues")
    return record, accepted


def critique_text(record: PostRecord) -> str:
    return json.dumps([i.model_dump() for i in record.draft.critic_issues], ensure_ascii=False)


def adapt(record: PostRecord, client: ModelClient, platforms: list[Platform]) -> PostRecord:
    """P7: platform variants. Code checks that no new claims were introduced (PRD
    agent 6 diff check) by requiring every numeral in a variant to appear in the
    script or title."""
    user = (
        f"{post_context(record)}\n"
        f"<brief>{record.brief.model_dump_json()}</brief>\n"
        f"<title>{record.draft.title}</title>\n<script>{record.draft.script}</script>\n"
        f"<caption>{record.draft.caption}</caption>\n"
        f"<platforms>{[p.value for p in platforms]}</platforms>"
    )
    r = client.run(spec("P7"), user, P7Output, static_blocks=[disclosure_block()])
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
        if AI_ROLE_DISCLOSURE_KEY not in (v.caption or record.draft.caption).lower():
            record.log(f"variant {v.platform} dropped: disclosure not preserved in the caption")
            continue
        record.variants.append(
            Variant(
                platform=Platform(v.platform),
                title=v.title,
                caption=v.caption,
                duration_s=v.duration_s,
                script_cut=v.script_cut,
                on_screen_text=v.on_screen_text,
                thumbnail_text=v.thumbnail_text,
            )
        )
    if not record.variants:
        raise AgentBlockedError("P7: no variant survived the code checks")
    return record


def write_canary(
    record: PostRecord,
    client: ModelClient,
    fault_type: FaultType,
    base_draft: dict,
    character_block: str = "",
    placement: str = "",
) -> PostRecord:
    """P14: a seeded-fault post for the queue. Flagged by code here, never by the
    model; the publisher refuses the flag (D23). The planted fault is stored so the
    card can reveal it and the spot check can target it."""
    user = (
        f"<fault_type>{fault_type.value}</fault_type>\n"
        f"<base_post_context>{post_context(record)}</base_post_context>\n"
        f"<placement>{placement}</placement>\n"
        f"<base_draft>{json.dumps(base_draft, ensure_ascii=False)}</base_draft>"
    )
    statics = [b for b in (character_block, disclosure_block()) if b]
    r = client.run(spec("P14"), user, P14Output, static_blocks=statics)
    if r.output is None or r.output.status == "blocked" or r.output.canary_draft is None:
        raise _blocked("P14", r)
    if r.output.planted_fault is None or r.output.planted_fault.type != fault_type:
        raise AgentBlockedError("P14: planted fault missing or of the wrong type")
    d = r.output.canary_draft
    record.draft.title, record.draft.script, record.draft.caption = d.title, d.script, d.caption
    record.flags.canary = True
    record.flags.canary_fault_type = fault_type
    record.flags.planted_fault = PlantedFault(**r.output.planted_fault.model_dump())
    record.log(f"canary written: fault={fault_type.value}")
    return record


def _numerals(text: str) -> list[str]:
    """Numbers as written, without the sentence punctuation that follows them ("2024."
    is 2024) and without thousands separators, so "4,000" and "4000" are one numeral."""
    return [n.replace(",", "") for n in re.findall(r"\d(?:[\d,.]*\d)?", text)]
