"""Whether a post is paid promotion, and whether its Ad prefix is in place (D3, R-03).

Pure rules over the record; the disclosure gate and the post context both read them.
"""

from __future__ import annotations

from cap.policy import AD_PREFIX, AFFILIATE_MARKERS
from cap.schema.post_record import PostRecord


def is_paid(record: PostRecord) -> bool:
    if record.sponsor is not None or record.brief.sponsor_id:
        return True
    text = " ".join([record.draft.title, record.draft.caption, record.draft.script]).lower()
    return any(m in text for m in AFFILIATE_MARKERS)


def ad_prefix_ok(record: PostRecord) -> bool:
    """When paid, every variant's title and caption start with the Ad token."""
    if not record.variants:
        return record.draft.title.startswith(AD_PREFIX) and record.draft.caption.startswith(
            AD_PREFIX
        )
    return all(
        v.title.startswith(AD_PREFIX) and v.caption.startswith(AD_PREFIX) for v in record.variants
    )
