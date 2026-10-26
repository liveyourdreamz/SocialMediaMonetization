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
    DraftOut,
    P2Call1Output,
    P2Call2Output,
    P3Output,
    P7Output,
    P14Output,
)
from cap.models import ModelClient, ModelResult, SourceDoc
from cap.policy import AI_ROLE_DISCLOSURE_KEY, disclosure_block
from cap.post_context import post_context
from cap.prompts import spec, tagged
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


def _script_of(r1: ModelResult[P2Call1Output]) -> DraftOut | None:
    """The drafted script, or None when the call was blocked or wrote nothing."""
    out = r1.output
    return None if out is None or out.status == "blocked" else out.draft


def _write_script(
    record: PostRecord,
    client: ModelClient,
    sources: list[SourceDoc],
    statics: list[str],
    limit: int,
    critique: str,
) -> None:
    """P2 call 1: the script and its surfaces onto the record. A refusal is retried
    once with the reason (D43); the opening-line slot is repaired to once, at the start."""
    user1 = (
        f"{post_context(record)}\n"
        f"<brief>{record.brief.model_dump_json()}</brief>\n"
        f"<opening_line_slot>{OPENING_LINE_SLOT}</opening_line_slot>\n"
        f"<claim_limit>Make at most {limit} factual claims in the whole script. A claim is "
        "a statement of fact a viewer could check, and every number is one. Explain the "
        "rest with reasoning and analogy, not further facts.</claim_limit>"
    )
    previous = record.draft.script

    def message(nonce: str, extra: str = "") -> str:
        text = user1
        if critique:  # the critic's notes and the draft are model-written (review R4)
            text += "\n" + tagged(nonce, critique=critique, previous_draft=previous)
        return text + extra

    r1 = client.run(spec("P2", "1"), message, P2Call1Output, sources=sources, static_blocks=statics)
    d = _script_of(r1)
    if d is None:
        # D43: one retry with the reason; every value it could be missing is in post_context
        why = r1.output.blocked_reason if r1.output else r1.reason
        record.log(f"drafter declined ({str(why)[:160]}); retrying with the reason")
        extra = (
            f"\n<previous_attempt_blocked>{why}</previous_attempt_blocked>\n"
            "<instruction>Every value this post needs is in post_context above and is "
            "given. Write the script now; where something is genuinely unknown, write "
            "around it rather than blocking.</instruction>"
        )
        r1 = client.run(
            spec("P2", "1"),
            lambda n: message(n, extra),
            P2Call1Output,
            sources=sources,
            static_blocks=statics,
        )
        d = _script_of(r1)
        if d is None:
            raise _blocked("P2 call 1", r1)
    record.draft.title, record.draft.script = d.title, d.script
    record.draft.shot_list, record.draft.caption = d.shot_list, d.caption
    record.draft.on_screen_text, record.draft.thumbnail_text = d.on_screen_text, d.thumbnail_text
    if record.draft.script.count(OPENING_LINE_SLOT) != 1:
        # D43: repaired, not parked: exactly one slot, at the start
        body = record.draft.script.replace(OPENING_LINE_SLOT, "").strip()
        record.draft.script = f"{OPENING_LINE_SLOT} {body}"
        record.log("repaired: the opening-line slot is now once, at the start")


def _list_claims(
    record: PostRecord,
    client: ModelClient,
    sources: list[SourceDoc],
    statics: list[str],
    limit: int,
    test_log: str,
) -> list[Claim]:
    """P2 call 2: the claims the script makes. A quote that is not in its cited source
    loses its link (gate 10 settles the claim); a declined list leaves it to the facts
    check."""
    r2 = client.run(
        spec("P2", "2"),
        lambda n: f"{post_context(record)}\n{tagged(n, script=record.draft.script)}",
        P2Call2Output,
        sources=sources,
        user_tail=f"<test_log>{test_log}</test_log>\n<claim_limit>List at most {limit} "
        "claims. Where one sentence states related facts, list it as one claim.</claim_limit>",
        static_blocks=statics,
    )
    if r2.output is None or r2.output.status == "blocked":
        why = r2.output.blocked_reason if r2.output else r2.reason
        record.log(f"claim list declined ({str(why)[:160]}); the facts check lists them")
        return []
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
    return claims


def _cover_numerals(record: PostRecord) -> None:
    """D43: a number the viewer reads that no claim lists becomes a claim, checked like
    every other (every surface, not just the script: review M4)."""
    covered = " ".join(c.text for c in record.draft.claims)
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
        added = _claims_for_numerals(missing, seen)
        record.draft.claims += added
        record.log(f"added {len(added)} claim(s) for numbers the drafter did not list: {missing}")


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
    _write_script(record, client, sources, statics, limit, critique)
    claims = _list_claims(record, client, sources, statics, limit, test_log)
    if len(claims) > limit and _refit:
        record.log(f"drafted {len(claims)} claims, over the limit of {limit}: rewriting to fit")
        note = (
            f"The script makes {len(claims)} factual claims; the limit is {limit}. Keep the "
            f"{limit} that carry the explanation, cut or merge the rest, and remove any "
            "number that is not among them."
        )
        critique = (critique + "\n" + note).strip()
        return draft(record, client, sources, character_block, test_log, critique, _refit=False)
    if len(claims) > limit:
        record.log(
            f"still {len(claims)} claims after a rewrite (limit {limit}, CAP_MAX_CLAIMS); "
            "continuing, every claim is checked"
        )
    record.draft.claims = claims
    record.draft.source_list = sorted(
        {c.source_url for c in claims if c.source_url and not c.source_url.startswith("testlog://")}
    )
    _cover_numerals(record)
    record.log(f"drafted: {len(record.draft.claims)} claims")
    return record


def _claims_for_numerals(numerals: list[str], text: str) -> list[Claim]:
    """The sentence (or line) that carries each unlisted number, as a claim to check."""
    pieces = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
    out: list[Claim] = []
    seen_text: set[str] = set()
    for n in numerals:
        hit = next((p for p in pieces if n in _numerals(p)), n)
        hit = hit.replace(OPENING_LINE_SLOT, "").strip() or n
        if hit not in seen_text:
            seen_text.add(hit)
            out.append(Claim(text=hit))
    return out


def critique(
    record: PostRecord, client: ModelClient, character_block: str = ""
) -> tuple[PostRecord, bool]:
    """P3, one pass only (PRD agent 5). The issues are stored on the record so they
    travel to the gates and are revealed on the card after the verdict (D33)."""
    draft_json = record.draft.model_dump_json(include={"title", "script", "caption", "claims"})

    def user(nonce: str) -> str:
        return (
            f"{post_context(record)}\n{tagged(nonce, draft=draft_json)}\n"
            f"<brief>{record.brief.model_dump_json()}</brief>"
        )

    r = client.run(
        spec("P3"), user, P3Output, static_blocks=[character_block] if character_block else None
    )
    if r.output is None or r.output.status == "blocked":
        why = r.output.blocked_reason if r.output else r.reason
        record.log(f"critic declined ({str(why)[:160]}); draft kept as written")
        return record, True
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

    def user(nonce: str) -> str:
        draft = tagged(
            nonce,
            title=record.draft.title,
            script=record.draft.script,
            caption=record.draft.caption,
        )
        return (
            f"{post_context(record)}\n<brief>{record.brief.model_dump_json()}</brief>\n"
            f"{draft}\n<platforms>{[p.value for p in platforms]}</platforms>"
        )

    r = client.run(spec("P7"), user, P7Output, static_blocks=[disclosure_block()])
    if r.output is None or r.output.status == "blocked":
        why = r.output.blocked_reason if r.output else r.reason
        record.log(f"adapter declined ({str(why)[:160]}); variants built from the draft")
        record.variants = _variants_from_draft(record, platforms)
        return record
    script_numerals = set(_numerals(record.draft.script + " " + record.draft.title))
    record.variants = []
    for v in r.output.variants:
        new_numbers = set(_numerals(v.title + " " + v.caption + " " + v.script_cut))
        new_numbers -= script_numerals
        if new_numbers:
            record.log(f"variant {v.platform} dropped: introduced numerals {sorted(new_numbers)}")
            continue
        caption = v.caption or record.draft.caption  # no caption: the draft's, as checked
        if AI_ROLE_DISCLOSURE_KEY not in caption.lower():
            record.log(f"variant {v.platform} dropped: disclosure not preserved in the caption")
            continue
        record.variants.append(
            Variant(
                platform=Platform(v.platform),
                title=v.title,
                caption=caption,
                duration_s=v.duration_s,
                script_cut=v.script_cut,
                on_screen_text=v.on_screen_text,
                thumbnail_text=v.thumbnail_text,
            )
        )
    if not record.variants:
        record.log("no adapted variant passed the checks; variants built from the draft")
        record.variants = _variants_from_draft(record, platforms)
    return record


def _variants_from_draft(record: PostRecord, platforms: list[Platform]) -> list[Variant]:
    """D43: the draft itself, per platform, with the disclosure in every caption. A
    Short is the whole script when the episode fits 60 s, else none."""
    from cap.policy import AI_ROLE_DISCLOSURE

    caption = record.draft.caption.strip()
    if AI_ROLE_DISCLOSURE_KEY not in caption.lower():
        caption = f"{caption} {AI_ROLE_DISCLOSURE}".strip()
    script = record.draft.script.replace(OPENING_LINE_SLOT, "").strip()
    out = []
    for p in platforms:
        if p == Platform.YOUTUBE_SHORT and record.brief.length_s > 60:
            continue
        out.append(
            Variant(
                platform=p,
                title=record.draft.title[:100],
                caption=caption,
                duration_s=record.brief.length_s,
                script_cut=script if p == Platform.YOUTUBE_SHORT else "",
                on_screen_text=record.draft.on_screen_text,
                thumbnail_text=record.draft.thumbnail_text,
            )
        )
    return out


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

    def user(nonce: str) -> str:
        return (
            f"<fault_type>{fault_type.value}</fault_type>\n"
            f"<base_post_context>{post_context(record)}</base_post_context>\n"
            f"<placement>{placement}</placement>\n"
            + tagged(nonce, base_draft=json.dumps(base_draft, ensure_ascii=False))
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
