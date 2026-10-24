"""The approval card as a state machine (D19, D33, D34). Every transition that must be
refused is asserted to raise; the reviewer's findings C1, C2, C4 and C8 are covered."""

import random

import pytest

from cap.domain.card import (
    CanaryMissedError,
    CardError,
    build_card,
    finalise,
    prepare_card,
    record_stage_one,
    record_verdict,
)
from cap.schema.post_record import (
    Channel,
    Claim,
    Decision,
    FaultType,
    PlantedFault,
    PostRecord,
    PostStatus,
    ReasonCode,
)

H = "hemant"


def _queued(channel: Channel = Channel.B, words: int = 400) -> PostRecord:
    r = PostRecord(channel_id=channel, status=PostStatus.AWAITING_APPROVAL)
    r.draft.title = "How desalination works"
    r.draft.script = "word " * words
    r.draft.claims = [
        Claim(
            text="reverse osmosis uses pressure",
            source_url="https://x",
            source_quote="pressure",
            status="supported",
        ),
        Claim(
            text="the Gulf has 40% of capacity",
            source_url="https://y",
            source_quote="40%",
            status="supported",
        ),
    ]
    r.gates.ethics.rule_ids = ["R-09"]
    prepare_card(r, random.Random(1))
    return r


def _approve(r: PostRecord, t: int = 300) -> PostRecord:
    return record_verdict(
        r,
        Decision.APPROVE,
        reviewer=H,
        time_on_card_s=t,
        opening_line="Here is the thing about salt.",
        spot_check_supported=True,
    )


def test_card_is_blind_until_verdict_then_reveals():
    r = _queued()
    card = build_card(r)
    assert card.gates is None and card.gates_revealed is False and card.critic_issues is None
    assert card.spot_check_claim in {c.text for c in r.draft.claims}
    assert card.expected_reading_s >= 30
    _approve(r)
    card2 = build_card(r)
    assert card2.gates_revealed and card2.gates["ethics"]["rule_ids"] == ["R-09"]
    assert r.approval.pre_reveal_decision == Decision.APPROVE
    assert r.human_contribution.text.startswith("Here is")


def test_build_card_does_not_mutate_and_needs_preparation():
    r = _queued()
    before = r.model_dump()
    build_card(r)
    assert r.model_dump() == before
    unprepared = PostRecord(channel_id=Channel.A, status=PostStatus.AWAITING_APPROVAL)
    with pytest.raises(CardError, match="not prepared"):
        build_card(unprepared)
    with pytest.raises(CardError, match="not prepared"):
        record_verdict(
            unprepared, Decision.REJECT, reviewer=H, time_on_card_s=5, reason_code=ReasonCode.OTHER
        )


def test_approve_requires_opening_line_spot_check_and_an_approver():
    r = _queued()
    with pytest.raises(CardError, match="opening line"):
        record_verdict(
            r,
            Decision.APPROVE,
            reviewer=H,
            time_on_card_s=300,
            opening_line="",
            spot_check_supported=True,
        )
    with pytest.raises(CardError, match="spot check"):
        record_verdict(
            r,
            Decision.APPROVE,
            reviewer=H,
            time_on_card_s=300,
            opening_line="x",
            spot_check_supported=None,
        )
    with pytest.raises(CardError, match="authorised approver"):
        record_verdict(
            r,
            Decision.APPROVE,
            reviewer="someone",
            time_on_card_s=300,
            opening_line="x",
            spot_check_supported=True,
        )
    assert r.approval.pre_reveal_decision is None  # nothing recorded by the refused calls


def test_too_fast_is_per_card_not_a_fixed_twenty_seconds():
    short = _queued(words=60)  # floor: 30 s
    record_verdict(
        short, Decision.REJECT, reviewer=H, time_on_card_s=25, reason_code=ReasonCode.OTHER
    )
    assert short.approval.too_fast is True
    long = _queued(words=1500)  # 1500 words -> 360 s expected
    assert 360 <= long.approval.expected_reading_s <= 370
    record_verdict(
        long, Decision.REJECT, reviewer=H, time_on_card_s=120, reason_code=ReasonCode.OTHER
    )
    assert long.approval.too_fast is True
    ok = _queued(words=1500)
    record_verdict(
        ok, Decision.REJECT, reviewer=H, time_on_card_s=400, reason_code=ReasonCode.OTHER
    )
    assert ok.approval.too_fast is False


def test_reject_needs_reason_chip():
    r = _queued()
    with pytest.raises(CardError, match="reason chip"):
        record_verdict(r, Decision.REJECT, reviewer=H, time_on_card_s=200)
    record_verdict(
        r, Decision.REJECT, reviewer=H, time_on_card_s=200, reason_code=ReasonCode.UNSOURCED_NUMBER
    )
    finalise(r, reviewer=H)
    assert r.status == PostStatus.REJECTED


def test_verdict_is_recorded_exactly_once():
    r = _queued()
    record_verdict(r, Decision.REJECT, reviewer=H, time_on_card_s=200, reason_code=ReasonCode.OTHER)
    with pytest.raises(CardError, match="already recorded"):
        _approve(r)
    assert r.approval.pre_reveal_decision == Decision.REJECT  # C4: never overwritten


def test_finalise_once_and_never_from_a_closed_state():
    r = _queued()
    with pytest.raises(CardError, match="no pre-reveal"):
        finalise(r, reviewer=H)
    record_verdict(r, Decision.REJECT, reviewer=H, time_on_card_s=200, reason_code=ReasonCode.OTHER)
    finalise(r, reviewer=H)
    assert r.status == PostStatus.REJECTED
    # C1: a rejected post cannot be flipped to approved afterwards
    with pytest.raises(CardError, match="finalise applies once"):
        finalise(
            r,
            reviewer=H,
            revised_decision=Decision.APPROVE,
            opening_line="x",
            spot_check_supported=True,
        )
    assert r.status == PostStatus.REJECTED and r.approval.token == ""
    # same for parked
    p = _queued()
    record_verdict(
        p, Decision.ESCALATE, reviewer=H, time_on_card_s=200, reason_code=ReasonCode.OTHER
    )
    finalise(p, reviewer=H)
    assert p.status == PostStatus.PARKED
    with pytest.raises(CardError):
        finalise(
            p,
            reviewer=H,
            revised_decision=Decision.APPROVE,
            opening_line="x",
            spot_check_supported=True,
        )
    # and no verdict after finalise
    with pytest.raises(CardError):
        record_verdict(
            p,
            Decision.APPROVE,
            reviewer=H,
            time_on_card_s=300,
            opening_line="x",
            spot_check_supported=True,
        )


def test_revision_to_approve_must_meet_d34_again():
    r = _queued()
    record_verdict(r, Decision.REJECT, reviewer=H, time_on_card_s=200, reason_code=ReasonCode.OTHER)
    with pytest.raises(CardError, match="opening line"):
        finalise(r, reviewer=H, revised_decision=Decision.APPROVE)
    assert r.status == PostStatus.AWAITING_APPROVAL
    finalise(
        r,
        reviewer=H,
        revised_decision=Decision.APPROVE,
        opening_line="Salt, then water.",
        spot_check_supported=True,
    )
    assert r.status == PostStatus.APPROVED and r.approval.revised_after_reveal
    assert (
        r.approval.pre_reveal_decision == Decision.REJECT
        and r.approval.decision == Decision.APPROVE
    )


def test_one_revision_after_reveal_and_same_reviewer():
    r = _queued()
    _approve(r)
    with pytest.raises(CardError, match="same|who recorded"):
        finalise(
            r, reviewer="other", revised_decision=Decision.REJECT, reason_code=ReasonCode.OTHER
        )
    finalise(
        r, reviewer=H, revised_decision=Decision.ESCALATE, reason_code=ReasonCode.OUT_GROUP_TITLE
    )
    assert r.status == PostStatus.PARKED and r.approval.revised_after_reveal is True


def test_channel_c_needs_stage_one_approval():
    r = _queued(channel=Channel.C)
    with pytest.raises(CardError, match="D19"):
        _approve(r)  # a blind approve on C is refused before stage one, nothing recorded
    assert r.approval.pre_reveal_decision is None
    with pytest.raises(CardError, match="Arabic reviewer"):
        record_stage_one(
            r, reviewer="nobody", decision=Decision.APPROVE, notes="", time_on_card_s=90
        )
    record_stage_one(
        r, reviewer="layla", decision=Decision.APPROVE, notes="fluent", time_on_card_s=90
    )
    with pytest.raises(CardError, match="already recorded"):
        record_stage_one(r, reviewer="layla", decision=Decision.REJECT, notes="", time_on_card_s=5)
    _approve(r)
    finalise(r, reviewer=H)
    assert r.status == PostStatus.APPROVED and r.approval.decided_at is not None


def test_canary_caught_only_with_matching_chip():
    def canary() -> PostRecord:
        c = _queued()
        c.flags.canary, c.flags.canary_fault_type = True, FaultType.OUT_GROUP_TITLE
        c.flags.planted_fault = PlantedFault(
            type=FaultType.OUT_GROUP_TITLE,
            span="THEIR grid",
            why_catchable="title sides against a nation",
        )
        return c

    c1 = canary()
    record_verdict(
        c1, Decision.REJECT, reviewer=H, time_on_card_s=200, reason_code=ReasonCode.LANGUAGE
    )
    with pytest.raises(CanaryMissedError):
        finalise(c1, reviewer=H)
    assert c1.flags.canary_outcome == "missed" and c1.status == PostStatus.DISCARDED

    c2 = canary()
    record_verdict(
        c2, Decision.REJECT, reviewer=H, time_on_card_s=200, reason_code=ReasonCode.OUT_GROUP_TITLE
    )
    finalise(c2, reviewer=H)
    assert c2.flags.canary_outcome == "caught" and c2.status == PostStatus.DISCARDED

    c3 = canary()
    _approve(c3)
    with pytest.raises(CanaryMissedError):
        finalise(c3, reviewer=H)
    assert c3.flags.canary_outcome == "missed" and c3.approval.token == ""


def test_planted_claim_is_the_spot_check_and_a_wrong_tap_is_a_miss():
    r = PostRecord(channel_id=Channel.A, status=PostStatus.AWAITING_APPROVAL)
    r.draft.script = "word " * 100
    r.draft.claims = [
        Claim(text="the sync takes 10 seconds", status="supported"),
        Claim(text="the battery lasts 40 hours", status="unsupported"),
    ]
    r.flags.canary, r.flags.canary_fault_type = True, FaultType.UNSOURCED_NUMBER
    r.flags.planted_fault = PlantedFault(type=FaultType.UNSOURCED_NUMBER, span="40 hours")
    prepare_card(r, random.Random(3))
    assert r.approval.spot_check.is_planted and "40 hours" in r.approval.spot_check.claim_text
    record_verdict(
        r,
        Decision.REJECT,
        reviewer=H,
        time_on_card_s=200,
        reason_code=ReasonCode.UNSOURCED_NUMBER,
        spot_check_supported=True,
    )
    with pytest.raises(CanaryMissedError):
        finalise(r, reviewer=H)
    assert r.flags.canary_outcome == "missed"
