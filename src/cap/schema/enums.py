"""The fixed vocabularies of the post record: channels, statuses, platforms, verdicts,
decisions, reason chips, fault types, release states."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class Channel(StrEnum):
    A = "A"  # Patch, English, AI tools
    B = "B"  # Falak, English, science
    C = "C"  # Falak, Arabic, science


class PostStatus(StrEnum):
    BRIEFED = "briefed"
    DRAFTING = "drafting"
    GATED = "gated"
    RENDERING = "rendering"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    REJECTED = "rejected"
    DISCARDED = "discarded"
    PARKED = "parked"  # an injection flag or a gate escalation parks; a human decides


class Platform(StrEnum):
    YOUTUBE_LONG = "youtube_long"
    YOUTUBE_SHORT = "youtube_short"
    FACEBOOK = "facebook"
    INSTAGRAM = "instagram"


class Lane(StrEnum):
    LOW = "low"
    HIGH = "high"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ESCALATE = "escalate"


class RuleOutcome(StrEnum):
    NOT_TRIGGERED = "not_triggered"
    REVISE = "revise"
    ESCALATE = "escalate"
    BLOCK = "block"


class Decision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"
    ESCALATE = "escalate"


class FaultType(StrEnum):
    """The seven canary fault types (Prompt Pack P14). A catch counts only when the
    reviewer's reason chip matches the planted type, so the chips use the same values."""

    UNSOURCED_NUMBER = "unsourced_number"
    MISSING_AD_PREFIX = "missing_ad_prefix"
    OUT_GROUP_TITLE = "out_group_title"
    FIRST_PERSON_HUMAN_CLAIM = "first_person_human_claim"
    SPONSOR_VERDICT = "sponsor_verdict"
    KIDS_STYLED_VISUAL = "kids_styled_visual"
    REUSED_COLD_OPEN = "reused_cold_open"


# D34 reason chips: the seven fault types, language, other. Fixed list; "other" above
# 20% of a week's rejects triggers a review of this list, not a free-text field.
class ReasonCode(StrEnum):
    UNSOURCED_NUMBER = "unsourced_number"
    MISSING_AD_PREFIX = "missing_ad_prefix"
    OUT_GROUP_TITLE = "out_group_title"
    FIRST_PERSON_HUMAN_CLAIM = "first_person_human_claim"
    SPONSOR_VERDICT = "sponsor_verdict"
    KIDS_STYLED_VISUAL = "kids_styled_visual"
    REUSED_COLD_OPEN = "reused_cold_open"
    LANGUAGE = "language"
    OTHER = "other"


class ReleaseStatus(StrEnum):
    NONE = "none"  # not approved yet, or approved before releases existed
    PREPARING = "preparing"  # the final render is running
    READY = "ready"  # final files, manifest and token in place
    FAILED = "failed"  # the final render or the token issue failed; `error` says why


ClaimStatus = Literal["pending", "supported", "unsupported", "outdated", "missing_source"]
