"""Version-locked policy text that prompts and gates must agree on. One place, so the
Drafter is told the same disclosure string the R-07 hard rule checks for, and the
reviewer allow-list that issues tokens is the one the publisher verifies against.

Changing anything here is a signed decision, like a prompt version.
"""

from __future__ import annotations

POLICY_VERSION = "1.0"

# R-07: the standard AI-role disclosure. Must appear in every variant's caption.
AI_ROLE_DISCLOSURE = "Presented by an AI character."
AI_ROLE_DISCLOSURE_KEY = "presented by an ai character"

# "Ad" first (Pack P9 header, D34 fault type missing_ad_prefix): a paid variant's title
# and caption start with this token.
AD_PREFIX = "Ad"

# Affiliate link markers used by the paid-promo detector (gate 9).
AFFILIATE_MARKERS = ("affiliate", "ref=", "?tag=", "utm_", "/go/", "partner=", "aff_")

# Approval token validity. A token older than this is refused by the publisher.
TOKEN_TTL_HOURS = 24 * 7


def disclosure_block() -> str:
    """The static system block P2 and P7 receive."""
    return (
        f'<disclosure_fields version="{POLICY_VERSION}">\n'
        f"Every caption ends with the exact sentence: {AI_ROLE_DISCLOSURE}\n"
        f'When the post is sponsored or carries an affiliate link, the title and the caption start with "{AD_PREFIX}".\n'
        "Disclosure fields are never shortened, moved or rephrased for any platform.\n"
        "</disclosure_fields>"
    )


# YouTube Partner Program bar from 1 February 2027 (R2; channels going live in week 9
# join under it). The Portfolio screen measures progress against these.
YPP_SUBSCRIBERS = 1_000
YPP_WATCH_HOURS_12MO = 8_000
YPP_SHORTS_VIEWS_90D = 20_000_000
YPP_BAR_DATE = "2027-02-01"

# A claim confirmed by Hemant's own test log cites this scheme, never a web link.
TESTLOG_SCHEME = "testlog://"
