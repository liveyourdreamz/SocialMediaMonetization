import random

import pytest

from cap.approval.card import CardError, build_card, finalise, record_verdict
from cap.schema.post_record import Channel, Claim, Decision, PostRecord, PostStatus, ReasonCode


def _queued() -> PostRecord:
    r = PostRecord(channel_id=Channel.B, status=PostStatus.AWAITING_APPROVAL)
    r.draft.title = "How desalination works"
    r.draft.script = "word " * 400
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
    return r


def test_card_is_blind_until_verdict_then_reveals():
    r = _queued()
    card = build_card(r, random.Random(1))
    assert card.gates is None and card.gates_revealed is False  # D33
    assert card.spot_check_claim in {c.text for c in r.draft.claims}  # D34
    assert card.expected_reading_s >= 30
    record_verdict(
        r,
        Decision.APPROVE,
        reviewer="hemant",
        time_on_card_s=120,
        opening_line="Here is the thing about salt.",
        spot_check_supported=True,
    )
    card2 = build_card(r)
    assert card2.gates_revealed and card2.gates["ethics"]["rule_ids"] == ["R-09"]
    assert r.approval.pre_reveal_decision == Decision.APPROVE
    assert r.human_contribution.text.startswith("Here is")


def test_approve_requires_opening_line_and_spot_check():
    r = _queued()
    build_card(r, random.Random(1))
    with pytest.raises(CardError):
        record_verdict(
            r,
            Decision.APPROVE,
            reviewer="hemant",
            time_on_card_s=120,
            opening_line="",
            spot_check_supported=True,
        )
    with pytest.raises(CardError):
        record_verdict(
            r,
            Decision.APPROVE,
            reviewer="hemant",
            time_on_card_s=120,
            opening_line="x",
            spot_check_supported=None,
        )


def test_reject_needs_reason_chip_and_is_one_step():
    r = _queued()
    build_card(r)
    with pytest.raises(CardError):
        record_verdict(r, Decision.REJECT, reviewer="hemant", time_on_card_s=20)
    record_verdict(
        r, Decision.REJECT, reviewer="hemant", time_on_card_s=20, reason_code=ReasonCode.FACT
    )
    assert r.approval.too_fast is True  # 20 s against a per-card threshold, not a fixed 20 s
    finalise(r)
    assert r.status == PostStatus.REJECTED


def test_one_revision_after_reveal():
    r = _queued()
    build_card(r, random.Random(2))
    record_verdict(
        r,
        Decision.APPROVE,
        reviewer="hemant",
        time_on_card_s=200,
        opening_line="Opening.",
        spot_check_supported=True,
    )
    finalise(r, revised_decision=Decision.ESCALATE, reason_code=ReasonCode.ETHICS)
    assert r.approval.pre_reveal_decision == Decision.APPROVE
    assert r.approval.decision == Decision.ESCALATE
    assert r.approval.revised_after_reveal is True
    assert r.status == PostStatus.PARKED


def test_canary_missed_is_logged_and_never_approved():
    r = _queued()
    r.flags.canary, r.flags.canary_fault_type = True, "fact"
    r.draft.claims[1].status = "unsupported"
    build_card(r, random.Random(3))
    assert r.approval.spot_check.is_planted
    record_verdict(
        r,
        Decision.APPROVE,
        reviewer="hemant",
        time_on_card_s=300,
        opening_line="Opening.",
        spot_check_supported=True,
    )
    finalise(r)
    assert r.status == PostStatus.DISCARDED
    assert any("canary MISSED" in h for h in r.history)
