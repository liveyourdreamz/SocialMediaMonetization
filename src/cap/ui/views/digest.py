"""The weekly digest page (PRD 4.16)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from cap.config import get_settings
from cap.ideas.week import monday_of
from cap.schema.ideas import Idea
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


# ---- this week's ideas (D48) --------------------------------------------------------


@dataclass
class IdeaRow:
    idea_id: str
    rank: int
    topic: str
    angle: str
    why_now: str
    sources: list[str]
    lane_high: bool
    timing: str
    score: str
    revenue: str
    status: str


def _why_now(i: Idea) -> str:
    if not i.demand_title:
        return "a constructive story this week; no Wikipedia rise matched"
    rise = f"{i.ratio_to_baseline:g}x its usual" if i.ratio_to_baseline else "new this month"
    return f"Wikipedia: “{i.demand_title}” {i.views_7d:,} views this week, {rise}"


def ideas_this_week(store: PostStore, today: date | None = None) -> dict[str, list[IdeaRow]]:
    """Each channel's ideas in this Monday's Digest, by rank, with the reasons in words."""
    monday = monday_of(today or datetime.now(UTC).date())
    out: dict[str, list[IdeaRow]] = {}
    for ch in get_settings().channels:
        out[ch] = [
            IdeaRow(
                idea_id=i.idea_id,
                rank=i.rank,
                topic=i.topic_edit or i.topic,
                angle=i.angle or "",
                why_now=_why_now(i),
                sources=i.evidence_urls,
                lane_high=i.risk_lane == "high",
                timing=i.timing,
                score=f"{i.score:.2f}",
                revenue=f"${i.revenue_low_usd:g} to ${i.revenue_high_usd:g} a post (assumed views)",
                status=i.status.value,
            )
            for i in store.ideas.week(ch, monday)
        ]
    return out
