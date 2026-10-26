"""The approval queue's waiting rules (D17 as amended by ADR-016, review R12).

While a card has waited a day for a verdict, new drafting pauses so work does not
pile up unseen; the posts already under way finish, and nothing is ever published by
waiting. Pure rules over records; the worker applies them and the pages show them.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from cap.schema.post_record import PostRecord


def waiting_since(record: PostRecord) -> datetime:
    """When the card entered the queue (posts from before queued_at: when filed)."""
    return record.approval.queued_at or record.created_at


def drafting_pause(queue: list[PostRecord], now: datetime, hours: int) -> str:
    """D17 as amended by ADR-016: while a card has waited `hours` or more for a verdict,
    new drafting pauses (the posts already under way finish); empty when it need not.
    Nothing is published either way: this only stops work piling up unseen."""
    waited = [p for p in queue if now - waiting_since(p) >= timedelta(hours=hours)]
    if not waited:
        return ""
    oldest = min(waited, key=waiting_since)
    days = (now - waiting_since(oldest)).total_seconds() / 86_400
    return (
        f"new drafts are paused: {len(waited)} post{'s' if len(waited) != 1 else ''} "
        f"waiting over {hours} hours for your verdict (the oldest {days:.1f} days)"
    )
