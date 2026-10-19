"""Approval card logic (PRD 4.5, D33, D34). Pure functions; the FastAPI app and the
tests both call these, so the rules live in one place.

D33: the card shows content, claims with source quotes and the media manifest. Gate
     and judge outcomes, near-miss scores and critic notes are hidden until the
     reviewer records a verdict; then they are revealed and one revision is allowed.
     Both verdicts are stored.
D34: reject and escalate are one tap plus a reason chip. Approve requires the opening
     line and one random-claim spot check. The too-fast flag is per card, from the
     expected reading time. A wrong tap on a canary's planted claim is a miss.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from cap.config import get_settings
from cap.schema.post_record import (
    Decision,
    PostRecord,
    PostStatus,
    ReasonCode,
    SpotCheck,
)

WORDS_PER_MINUTE = 200  # reading speed used for the per-card threshold
MIN_READ_SECONDS = 30


def expected_reading_seconds(record: PostRecord) -> int:
    words = len(record.draft.script.split()) + len(record.draft.title.split())
    words += sum(len(c.text.split()) for c in record.draft.claims)
    return max(MIN_READ_SECONDS, int(words / WORDS_PER_MINUTE * 60 * 0.5))  # half the full read


@dataclass
class CardView:
    """What the reviewer sees. Built from the record; gate fields are absent, not
    blanked, while the card is blind."""

    post_id: str
    channel_id: str
    title: str
    script: str
    caption: str
    claims: list[dict] = field(default_factory=list)
    media_manifest: dict = field(default_factory=dict)
    spot_check_claim: str | None = None
    spot_check_quote: str | None = None
    expected_reading_s: int = 0
    gates_revealed: bool = False
    gates: dict | None = None  # only after reveal


def pick_spot_check(record: PostRecord, rng: random.Random | None = None) -> SpotCheck | None:
    """One claim at random with its source quote (D34). On a canary, prefer the planted
    claim so the spot check doubles as the canary probe."""
    rng = rng or random.Random()
    claims = [c for c in record.draft.claims if c.text]
    if not claims:
        return None
    if record.flags.canary and record.flags.canary_fault_type == "fact":
        planted = [c for c in claims if c.status != "supported"]
        if planted:
            c = rng.choice(planted)
            return SpotCheck(claim_text=c.text, actually_supported=False, is_planted=True)
    c = rng.choice(claims)
    return SpotCheck(claim_text=c.text, actually_supported=(c.status == "supported"))


def build_card(record: PostRecord, rng: random.Random | None = None) -> CardView:
    s = get_settings()
    if record.approval.spot_check is None:
        record.approval.spot_check = pick_spot_check(record, rng)
    if record.approval.expected_reading_s == 0:
        record.approval.expected_reading_s = expected_reading_seconds(record)
    sc = record.approval.spot_check
    quote = None
    if sc:
        quote = next((c.source_quote for c in record.draft.claims if c.text == sc.claim_text), None)
    revealed = not (s.blind_first_enabled and record.flags.gate_blind)
    return CardView(
        post_id=record.post_id,
        channel_id=record.channel_id.value,
        title=record.draft.title,
        script=record.draft.script,
        caption=record.draft.caption,
        claims=[
            {"text": c.text, "source_url": c.source_url, "source_quote": c.source_quote}
            for c in record.draft.claims
        ],
        media_manifest={
            "ai_label": record.gates.disclosure.ai_label,
            "asset_uri": record.asset.uri,
        },
        spot_check_claim=sc.claim_text if sc else None,
        spot_check_quote=quote,
        expected_reading_s=record.approval.expected_reading_s,
        gates_revealed=revealed,
        gates=record.gates.model_dump() if revealed else None,
    )


class CardError(ValueError):
    pass


def record_verdict(
    record: PostRecord,
    decision: Decision,
    *,
    reviewer: str,
    time_on_card_s: int,
    reason_code: ReasonCode | None = None,
    opening_line: str | None = None,
    spot_check_supported: bool | None = None,
) -> PostRecord:
    """First verdict on a blind card. Validates D34's requirements, stores the
    pre-reveal decision, reveals the gates. Does not issue a token: that happens in
    `finalise` after the reviewer has seen the gates and had the chance to revise."""
    if record.status != PostStatus.AWAITING_APPROVAL:
        raise CardError(f"post is {record.status.value}, not awaiting approval")
    a = record.approval
    if decision in (Decision.REJECT, Decision.ESCALATE):
        if reason_code is None:
            raise CardError("reject and escalate need a reason chip")
    if decision == Decision.APPROVE:
        if not opening_line or not opening_line.strip():
            raise CardError("approve needs the opening line (human contribution)")
        if a.spot_check is not None and spot_check_supported is None:
            raise CardError("approve needs the spot check answered")
        record.human_contribution.text = opening_line.strip()
        record.human_contribution.author = reviewer
    if a.spot_check is not None and spot_check_supported is not None:
        a.spot_check.reviewer_said_supported = spot_check_supported
    a.reviewer = reviewer
    a.time_on_card_s = time_on_card_s
    a.too_fast = time_on_card_s < a.expected_reading_s
    a.reason_code = reason_code
    a.pre_reveal_decision = decision
    a.decision = decision
    a.canary = record.flags.canary
    record.flags.gate_blind = False  # D33: reveal now
    record.log(
        f"verdict {decision.value} pre-reveal by {reviewer} in {time_on_card_s}s (too_fast={a.too_fast})"
    )
    return record


def finalise(
    record: PostRecord,
    *,
    revised_decision: Decision | None = None,
    reason_code: ReasonCode | None = None,
) -> PostRecord:
    """Second step: the reviewer has seen the gates; may revise once (D33). Applies
    the status change and the canary outcome. Token issue is the caller's job (needs
    the asset hash)."""
    a = record.approval
    if a.pre_reveal_decision is None:
        raise CardError("no pre-reveal verdict recorded")
    if revised_decision is not None and revised_decision != a.pre_reveal_decision:
        if revised_decision in (Decision.REJECT, Decision.ESCALATE) and reason_code is None:
            raise CardError("a revised reject or escalate needs a reason chip")
        a.decision = revised_decision
        a.reason_code = reason_code or a.reason_code
        a.revised_after_reveal = True
        record.log(f"verdict revised after reveal to {revised_decision.value}")
    # canary outcome (P14 zero-miss): approving a canary is a miss; so is a wrong tap
    # on its planted claim
    if record.flags.canary:
        missed = a.decision == Decision.APPROVE
        if a.spot_check and a.spot_check.is_planted and a.spot_check.reviewer_said_supported:
            missed = True
        record.log(
            f"canary {'MISSED' if missed else 'caught'}: fault={record.flags.canary_fault_type} reason={a.reason_code}"
        )
        record.status = PostStatus.DISCARDED  # canaries never go further (D23)
        return record
    record.status = {
        Decision.APPROVE: PostStatus.APPROVED,
        Decision.REJECT: PostStatus.REJECTED,
        Decision.ESCALATE: PostStatus.PARKED,
    }[a.decision or Decision.ESCALATE]
    return record
