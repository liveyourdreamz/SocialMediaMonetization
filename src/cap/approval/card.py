"""Approval card logic (PRD 4.5, D19, D33, D34). Pure functions; the FastAPI app and the
tests both call these, so the rules live in one place.

State machine for one post:

  awaiting_approval, card_prepared=False
    -> prepare_card()            at queue time: spot check and reading time fixed
  awaiting_approval, gate_blind=True
    -> record_verdict()          exactly once; D34 checks; stores pre-reveal verdict
  awaiting_approval, gate_blind=False
    -> finalise()                exactly once; optional single revision (D33); a
                                 revised approve must meet the D34 checks again
  approved | rejected | parked | discarded (canary)

Nothing moves a post out of approved/rejected/parked here. Reopening is an operator
action with its own audit line, not a card action.

D33: the card shows content, claims with source quotes and the media manifest. Gate
     and judge outcomes, near-miss scores and critic notes are hidden until the
     reviewer records a verdict; then they are revealed and one revision is allowed.
D34: reject and escalate are one tap plus a reason chip. Approve requires the opening
     line and one random-claim spot check. The too-fast flag is per card. A canary is
     caught only when the reason chip matches the planted fault type; a wrong tap on
     its planted claim is a miss.
D19: channel C needs the Arabic reviewer's stage-one approval before Hemant's.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime

from cap.config import get_settings
from cap.schema.post_record import (
    Channel,
    Decision,
    PostRecord,
    PostStatus,
    ReasonCode,
    SpotCheck,
    StageOneReview,
)

WORDS_PER_MINUTE = 200  # reading speed used for the per-card threshold
MIN_READ_SECONDS = 30
READ_SHARE = 0.8  # a reviewer who read less than 80% of the full read time is too fast


class CardError(ValueError):
    pass


class CanaryMissedError(RuntimeError):
    """P14 zero-miss: raised after the record is updated so the caller pauses the
    session. The record itself is already discarded."""


def expected_reading_seconds(record: PostRecord) -> int:
    words = len(record.draft.script.split()) + len(record.draft.title.split())
    words += sum(len(c.text.split()) for c in record.draft.claims)
    return max(MIN_READ_SECONDS, int(words / WORDS_PER_MINUTE * 60 * READ_SHARE))


def pick_spot_check(record: PostRecord, rng: random.Random | None = None) -> SpotCheck | None:
    """One claim at random with its source quote (D34). On a canary whose planted
    fault lies in a claim, the spot check is that claim, matched by span."""
    rng = rng or random.Random()
    claims = [c for c in record.draft.claims if c.text]
    if not claims:
        return None
    pf = record.flags.planted_fault
    if record.flags.canary and pf is not None:
        planted = [c for c in claims if pf.span and pf.span in c.text]
        if planted:
            return SpotCheck(claim_text=planted[0].text, actually_supported=False, is_planted=True)
    c = rng.choice(claims)
    return SpotCheck(claim_text=c.text, actually_supported=(c.status == "supported"))


def prepare_card(record: PostRecord, rng: random.Random | None = None) -> PostRecord:
    """Called once when the post enters the queue. Fixes the spot check and the reading
    threshold so a client cannot skip them by never fetching the card."""
    if record.status != PostStatus.AWAITING_APPROVAL:
        raise CardError(f"post is {record.status.value}, not awaiting approval")
    record.approval.spot_check = pick_spot_check(record, rng)
    record.approval.expected_reading_s = expected_reading_seconds(record)
    record.flags.card_prepared = True
    return record


@dataclass
class CardView:
    """What the reviewer sees. Gate fields are absent, not blanked, while blind."""

    post_id: str
    channel_id: str
    title: str
    script: str
    caption: str
    on_screen_text: list[str] = field(default_factory=list)
    thumbnail_text: str = ""
    claims: list[dict] = field(default_factory=list)
    media_manifest: dict = field(default_factory=dict)
    sponsor: dict | None = None
    stage_one: dict | None = None
    spot_check_claim: str | None = None
    spot_check_quote: str | None = None
    expected_reading_s: int = 0
    gates_revealed: bool = False
    gates: dict | None = None  # only after reveal
    critic_issues: list[dict] | None = None  # only after reveal
    planted_fault: dict | None = None  # only after reveal, canaries only


def build_card(record: PostRecord) -> CardView:
    """Read-only view; never mutates the record."""
    if not record.flags.card_prepared:
        raise CardError("card not prepared; prepare_card() runs at queue time")
    s = get_settings()
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
        on_screen_text=list(record.draft.on_screen_text),
        thumbnail_text=record.draft.thumbnail_text,
        claims=[
            {
                "text": c.text,
                "source_url": c.source_url,
                "source_quote": c.source_quote,
                "status": c.status,
            }
            for c in record.draft.claims
        ],
        media_manifest=record.media_manifest.model_dump(),
        sponsor=record.sponsor.model_dump() if record.sponsor else None,
        stage_one=record.approval.stage_one.model_dump() if record.approval.stage_one else None,
        spot_check_claim=sc.claim_text if sc else None,
        spot_check_quote=quote,
        expected_reading_s=record.approval.expected_reading_s,
        gates_revealed=revealed,
        gates=record.gates.model_dump(by_alias=True) if revealed else None,
        critic_issues=[i.model_dump() for i in record.draft.critic_issues] if revealed else None,
        planted_fault=(
            record.flags.planted_fault.model_dump()
            if revealed and record.flags.planted_fault
            else None
        ),
    )


def _check_approve_inputs(
    record: PostRecord, opening_line: str | None, spot_check_supported: bool | None
) -> None:
    if not opening_line or not opening_line.strip():
        raise CardError("approve needs the opening line (human contribution)")
    if record.approval.spot_check is not None and spot_check_supported is None:
        raise CardError("approve needs the spot check answered")


def record_stage_one(
    record: PostRecord, *, reviewer: str, decision: Decision, notes: str, time_on_card_s: int
) -> PostRecord:
    """D19: the Arabic reviewer's pass on channel C. Does not change status; Hemant's
    verdict follows."""
    if record.channel_id != Channel.C:
        raise CardError("stage-one review applies to channel C only")
    if reviewer not in get_settings().arabic_reviewers:
        raise CardError("reviewer is not an authorised Arabic reviewer")
    if record.status != PostStatus.AWAITING_APPROVAL:
        raise CardError(f"post is {record.status.value}, not awaiting approval")
    if record.approval.stage_one is not None:
        raise CardError("stage one already recorded")
    record.approval.stage_one = StageOneReview(
        reviewer=reviewer, decision=decision, notes=notes, time_on_card_s=time_on_card_s
    )
    record.log(f"stage one {decision.value} by {reviewer}")
    return record


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
    pre-reveal decision, reveals the gates. Exactly once per post."""
    if record.status != PostStatus.AWAITING_APPROVAL:
        raise CardError(f"post is {record.status.value}, not awaiting approval")
    if not record.flags.card_prepared:
        raise CardError("card not prepared")
    a = record.approval
    if a.pre_reveal_decision is not None:
        raise CardError("verdict already recorded; use finalise")
    if reviewer not in get_settings().approvers:
        raise CardError("reviewer is not an authorised approver (D5)")
    if decision in (Decision.REJECT, Decision.ESCALATE) and reason_code is None:
        raise CardError("reject and escalate need a reason chip")
    if decision == Decision.APPROVE:
        _check_approve_inputs(record, opening_line, spot_check_supported)
        _check_stage_one(record)
        record.human_contribution.text = (opening_line or "").strip()
        record.human_contribution.author = reviewer
    if a.spot_check is not None and spot_check_supported is not None:
        a.spot_check.reviewer_said_supported = spot_check_supported
    a.reviewer = reviewer
    a.time_on_card_s = time_on_card_s
    a.too_fast = time_on_card_s < a.expected_reading_s
    a.reason_code = reason_code
    a.pre_reveal_reason_code = reason_code
    a.pre_reveal_decision = decision
    a.decision = decision
    record.flags.gate_blind = False  # D33: reveal now
    record.log(
        f"verdict {decision.value} pre-reveal by {reviewer} in {time_on_card_s}s "
        f"(too_fast={a.too_fast})"
    )
    return record


def finalise(
    record: PostRecord,
    *,
    reviewer: str,
    revised_decision: Decision | None = None,
    reason_code: ReasonCode | None = None,
    opening_line: str | None = None,
    spot_check_supported: bool | None = None,
    acknowledged: bool = False,
) -> PostRecord:
    """Second step: the reviewer has seen the gates; may revise once (D33). Applies
    the status change and the canary outcome. Token issue is the caller's job (needs
    the asset hash). Exactly once per post."""
    if record.status != PostStatus.AWAITING_APPROVAL:
        raise CardError(f"post is {record.status.value}; finalise applies once, while awaiting")
    a = record.approval
    if a.pre_reveal_decision is None or record.flags.gate_blind:
        raise CardError("no pre-reveal verdict recorded")
    if reviewer != a.reviewer:
        raise CardError("finalise must come from the reviewer who recorded the verdict")

    if record.flags.canary:
        # P14: a canary is scored on the blind verdict only. The reveal names the fault,
        # so a revision after it would be a catch with the answer in hand.
        if revised_decision is not None and revised_decision != a.pre_reveal_decision:
            record.log("revision after reveal ignored: canaries are scored blind")
        return _finalise_canary(record)

    revising = revised_decision is not None and revised_decision != a.pre_reveal_decision
    decision: Decision = (
        revised_decision
        if revising and revised_decision is not None
        else (a.decision or Decision.ESCALATE)
    )
    # validate everything before touching the record (a refused revision leaves it intact)
    if revising:
        if decision in (Decision.REJECT, Decision.ESCALATE) and reason_code is None:
            raise CardError("a revised reject or escalate needs a reason chip")
        if decision == Decision.APPROVE:
            _check_approve_inputs(record, opening_line, spot_check_supported)
    if decision == Decision.APPROVE:
        _check_stage_one(record)
        if record.flags.needs_decision and not acknowledged:
            raise CardError(
                "this post has questions only you can settle (shown above); tick that you "
                "reviewed them to approve"
            )
    if revising:
        if decision == Decision.APPROVE:
            record.human_contribution.text = (opening_line or "").strip()
            record.human_contribution.author = reviewer
            if a.spot_check is not None:
                a.spot_check.reviewer_said_supported = spot_check_supported
        a.decision = decision
        a.reason_code = reason_code or a.reason_code
        a.revised_after_reveal = True
        record.log(f"verdict revised after reveal to {decision.value}")
    record.status = {
        Decision.APPROVE: PostStatus.APPROVED,
        Decision.REJECT: PostStatus.REJECTED,
        Decision.ESCALATE: PostStatus.PARKED,
    }[decision]
    a.decided_at = datetime.now(UTC)
    if decision == Decision.APPROVE and record.flags.needs_decision:
        a.decisions_acknowledged = list(record.flags.needs_decision)
        record.log(
            f"{reviewer} reviewed and accepted: "
            + "; ".join(n.split(":")[0] for n in record.flags.needs_decision)
        )
    if decision != Decision.APPROVE:
        chip = a.reason_code.value.replace("_", " ") if a.reason_code else "no reason chip"
        record.stop(
            "review",
            f"{'rejected' if decision == Decision.REJECT else 'escalated'} by {reviewer}: {chip}",
        )
    if decision == Decision.ESCALATE:
        record.log(
            f"parked: escalated by {reviewer}: {a.reason_code.value if a.reason_code else 'no chip'}"
        )
    return record


def _check_stage_one(record: PostRecord) -> None:
    """D19: channel C cannot be approved, blind or revised, without stage one."""
    if record.channel_id == Channel.C:
        so = record.approval.stage_one
        if so is None or so.decision != Decision.APPROVE:
            raise CardError("channel C needs the Arabic reviewer's stage-one approval (D19)")


def _finalise_canary(record: PostRecord) -> PostRecord:
    """P14: scored on the blind verdict and its chip. A catch needs the matching reason
    chip; approving, or a wrong tap on the planted claim, is a miss. Canaries never go
    further (D23)."""
    a = record.approval
    decision = a.pre_reveal_decision or Decision.ESCALATE
    chip = a.pre_reveal_reason_code
    fault = record.flags.canary_fault_type
    matched = (
        decision != Decision.APPROVE
        and chip is not None
        and fault is not None
        and chip.value == fault.value
    )
    wrong_tap = bool(
        a.spot_check and a.spot_check.is_planted and a.spot_check.reviewer_said_supported
    )
    missed = decision == Decision.APPROVE or wrong_tap or not matched
    record.flags.canary_outcome = "missed" if missed else "caught"
    record.status = PostStatus.DISCARDED
    record.stop(
        "review",
        f"a canary (a planted fault): you {'missed' if missed else 'caught'} it; never published",
    )
    a.decided_at = datetime.now(UTC)
    record.log(
        f"canary {record.flags.canary_outcome.upper()}: fault={fault.value if fault else None} "
        f"chip={chip.value if chip else None} decision={decision.value}"
    )
    if missed:
        raise CanaryMissedError(record.post_id)
    return record
