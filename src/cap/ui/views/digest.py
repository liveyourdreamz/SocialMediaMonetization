"""The weekly digest page (PRD 4.16)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cap.config import get_settings
from cap.schema.post_record import Decision, ReasonCode
from cap.store import PostStore
from cap.ui.views.portfolio import _finalised
from cap.ui.views.text import REASON_TEXT, day


@dataclass
class Digest:
    week: str
    approved: int
    rejected: int
    escalated: int
    reasons: list[tuple[str, int]]
    other_share: float | None
    other_flag: bool
    critic_flag_rate: float | None
    reviewer_reject_rate: float | None
    agreement: float | None
    revised: int
    canaries_caught: int
    canaries_missed: int
    proposals: list[str]


def digest(store: PostStore) -> Digest:
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    week = [
        p
        for p in _finalised(store)
        if p.approval.decided_at and p.approval.decided_at >= since and not p.flags.canary
    ]
    approved = sum(1 for p in week if p.approval.decision == Decision.APPROVE)
    rejected = sum(1 for p in week if p.approval.decision == Decision.REJECT)
    escalated = sum(1 for p in week if p.approval.decision == Decision.ESCALATE)
    counts: dict[str, int] = {}
    for p in week:
        if p.approval.decision in (Decision.REJECT, Decision.ESCALATE) and p.approval.reason_code:
            k = REASON_TEXT[p.approval.reason_code]
            counts[k] = counts.get(k, 0) + 1
    rejects = rejected + escalated
    other = counts.get(REASON_TEXT[ReasonCode.OTHER], 0)
    other_share = other / rejects if rejects else None
    critic = [p for p in week if p.draft.critic_verdict]
    critic_rate = (
        sum(1 for p in critic if p.draft.critic_verdict == "revise") / len(critic)
        if critic
        else None
    )
    pairs = [p for p in week if p.approval.pre_reveal_decision and p.approval.decision]
    agreement = (
        sum(1 for p in pairs if p.approval.pre_reveal_decision == p.approval.decision) / len(pairs)
        if pairs
        else None
    )
    canaries = [
        p
        for p in store.all()
        if p.flags.canary and p.approval.decided_at and p.approval.decided_at >= since
    ]
    return Digest(
        week=f"{day(since, year=False)} to {day(now)}",
        approved=approved,
        rejected=rejected,
        escalated=escalated,
        reasons=sorted(counts.items(), key=lambda kv: -kv[1]),
        other_share=other_share,
        other_flag=bool(other_share and other_share > get_settings().reject_other_share_flag),
        critic_flag_rate=critic_rate,
        reviewer_reject_rate=rejects / len(week) if week else None,
        agreement=agreement,
        revised=sum(1 for p in week if p.approval.revised_after_reveal),
        canaries_caught=sum(1 for p in canaries if p.flags.canary_outcome == "caught"),
        canaries_missed=sum(1 for p in canaries if p.flags.canary_outcome == "missed"),
        proposals=[],  # the learning loop (D35) proposes after the first monetised month
    )
