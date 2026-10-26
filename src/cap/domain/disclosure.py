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
    """When paid, every title and caption that can be published starts with the Ad
    token: each variant's, and the draft's, because a platform without its own variant
    is published with the draft's text (review R5: a dropped long-form variant sent the
    unchecked draft title)."""
    texts = [record.draft.title, record.draft.caption]
    texts += [t for v in record.variants for t in (v.title, v.caption)]
    return all(t.startswith(AD_PREFIX) for t in texts)
