"""The Portfolio: per-channel figures, each with its source (D24: code does the arithmetic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from statistics import median
from typing import Any

from cap import policy
from cap.config import get_settings
from cap.schema.post_record import PostRecord, PostStatus
from cap.store import PostStore
from cap.ui.views.text import add_months, channel_label, day, is_rtl, minutes, word_count


@dataclass
class Figure:
    label: str
    value: str
    meta: str


@dataclass
class ChannelCard:
    channel: str
    label: str
    rtl: bool
    status: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    progress: float | None = None
    progress_text: str = ""
    note: str = ""


@dataclass
class Portfolio:
    month: str
    note: str
    queue_count: int
    queue_minutes: int
    queue_oldest_days: int
    figures: list[Figure]
    channels: list[ChannelCard]
    gate_title: str
    gate_body: str
    gate_meta: str
    sources: str
    published_count: int = 0
    approved_waiting: int = 0


CLOSED = (PostStatus.APPROVED, PostStatus.REJECTED, PostStatus.PARKED, PostStatus.PUBLISHED)


def _finalised(store: PostStore) -> list[PostRecord]:
    """Posts a reviewer has closed (not canaries, not pending revisions)."""
    return [p for p in store.all() if p.status in CLOSED and p.approval.decided_at]


def canary_record(store: PostStore) -> tuple[int, int]:
    caught = sum(1 for p in store.all() if p.flags.canary_outcome == "caught")
    missed = sum(1 for p in store.all() if p.flags.canary_outcome == "missed")
    return caught, missed


def canary_rate(store: PostStore) -> str:
    """The rate the next card is seeded at (cap.domain.canary, the one rule)."""
    from cap.demo.canary_job import closed_cards
    from cap.domain.canary import rate

    return f"1 in {rate(closed_cards(store))}"


def portfolio(store: PostStore) -> Portfolio:
    s = get_settings()
    now = datetime.now(UTC)
    q = store.queue()
    oldest = max(((now - p.created_at).days for p in q), default=0)
    a = store.state.analytics
    chans: dict[str, dict[str, Any]] = a.get("channels", {})
    port: dict[str, Any] = a.get("portfolio", {})

    # figures
    approved_posts = [
        p for p in store.all() if p.status in (PostStatus.APPROVED, PostStatus.PUBLISHED)
    ]
    if "revenue_usd" in port and approved_posts:
        rev = Figure(
            "Revenue / approved post",
            f"${port['revenue_usd'] / len(approved_posts):,.2f}",
            port.get("revenue_note", ""),
        )
    elif "revenue_usd" in port:
        rev = Figure("Revenue / approved post", "$0.00", "no approved posts in the period")
    else:
        rev = Figure("Revenue / approved post", "—", "no channel monetised yet")
    if "cost_per_post_usd" in port:
        cost = Figure(
            "Cost / approved post", f"${port['cost_per_post_usd']:,.2f}", port.get("cost_note", "")
        )
    else:
        cost = Figure("Cost / approved post", "—", "no cost ledger yet")
    times = [
        p.approval.time_on_card_s
        for p in _finalised(store)
        if p.approval.time_on_card_s > 0 and not p.flags.canary
    ]
    rt = Figure(
        "Your review time",
        minutes(int(median(times))) if times else "—",
        "median per card · target 30" if times else "no cards reviewed yet",
    )
    c, m = canary_record(store)
    can = Figure(
        "Canaries",
        f"{c} caught · {m} missed",
        f"rate {canary_rate(store)} · 1 in 3 for 10 cards after a miss",
    )

    # channels
    cards = []
    for ch in s.channels:
        label = channel_label(ch)
        d = chans.get(ch)
        month_posts = [
            p
            for p in approved_posts
            if p.channel_id.value == ch
            and p.created_at.year == now.year
            and p.created_at.month == now.month
        ]
        longform = sum(1 for p in month_posts if word_count(p) >= 220)
        shorts = len(month_posts) - longform
        card = ChannelCard(ch, label, is_rtl(ch), "")
        if not d:
            card.status = "not live"
            card.note = (
                "Waiting on: the channel, the first pull by agent 14."
                if ch != "C"
                else "Launches in month 3 of B. Waiting on: the Arabic reviewer's name in the allow-list, the Falak Arabic voice test."
            )
            cards.append(card)
            continue
        card.status = d.get("status", "live")
        card.rows = [
            (
                "This month",
                f"{longform} of {s.longform_per_channel_per_month} long-form · {shorts} of {s.shorts_cap_per_channel_per_month} Shorts",
            ),
            (
                "Average % viewed",
                f"{d['avg_percent_viewed']}% (long-form, 28 days)"
                if "avg_percent_viewed" in d
                else "no pull yet",
            ),
            (
                "Watch hours, 12 mo",
                f"{d['watch_hours_12mo']:,} of {policy.YPP_WATCH_HOURS_12MO:,} needed"
                if "watch_hours_12mo" in d
                else "no pull yet",
            ),
            (
                "Subscribers",
                f"{d['subscribers']:,} of {policy.YPP_SUBSCRIBERS:,} needed"
                if "subscribers" in d
                else "no pull yet",
            ),
            ("Monetised", d.get("monetised", "not yet · YPP bar rises 1 Feb 2027")),
        ]
        if "watch_hours_12mo" in d:
            share = min(1.0, d["watch_hours_12mo"] / policy.YPP_WATCH_HOURS_12MO)
            card.progress = share
            pace = d.get("pace_note", "")
            card.progress_text = f"{round(share * 100)}% of the watch-hour threshold" + (
                f" · {pace}" if pace else ""
            )
        cards.append(card)

    # kill gate
    if s.pilot_start:
        start = date.fromisoformat(s.pilot_start)
        gates = [
            (3, add_months(start, 3)),
            (6, add_months(start, 6)),
            (9, add_months(start, 9)),
            (12, add_months(start, 12)),
        ]
        nxt = next(((n, d) for n, d in gates if d >= now.date()), gates[-1])
        gate_title = f"Month {nxt[0]} review · {day(nxt[1])}"
        gate_body = (
            f"What must be true: [R1 month-{nxt[0]} threshold, re-run for the Feb 2027 YPP bar]."
        )
        gate_meta = f"Pilot started {day(start)}."
    else:
        gate_title, gate_body, gate_meta = (
            "Not started",
            "The kill-gate clock starts the day channels A and B go live (PRD 8).",
            "Set CAP_PILOT_START at go-live.",
        )

    src_parts = []
    src_parts.append(
        f"{a['source']}, pulled {a['pulled_at']}"
        if a.get("source")
        else "YouTube Analytics: no pull yet (agent 14 not running)"
    )
    src_parts.append(port.get("revenue_source", "AdSense: no payout yet"))
    src_parts.append(port.get("cost_source", "costs: no ledger yet"))
    return Portfolio(
        month=now.strftime("%B %Y"),
        note=store.state.analytics_note,
        queue_count=len(q),
        queue_minutes=round(sum(p.approval.expected_reading_s for p in q) / 60),
        queue_oldest_days=oldest,
        figures=[rev, cost, rt, can],
        channels=cards,
        gate_title=gate_title,
        gate_body=gate_body,
        gate_meta=gate_meta,
        sources=" · ".join(src_parts),
        published_count=sum(1 for p in store.all() if p.status == PostStatus.PUBLISHED),
        approved_waiting=sum(1 for p in store.all() if p.status == PostStatus.APPROVED),
    )
