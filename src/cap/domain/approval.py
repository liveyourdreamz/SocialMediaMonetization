"""Whether a human approval holds for release (D5, D19, R-20, PRD 4.6): re-verified
from what the card recorded, never from the status alone, plus the gates. Pure rules;
cap.release raises the refusal before anything is sealed or sent."""

from __future__ import annotations

from cap.config import Settings
from cap.domain.verdict import gates_cleared
from cap.schema.post_record import Channel, Decision, PostRecord, PostStatus


def _not_canary(r: PostRecord, s: Settings) -> str | None:
    return "flagged record: canary posts are never published" if r.flags.canary else None


def _approved_status(r: PostRecord, s: Settings) -> str | None:
    # published counts too: a retry sends only the destinations that have no id yet
    # (the publisher skips the rest), so a platform that failed can be tried again
    ok = r.status in (PostStatus.APPROVED, PostStatus.PUBLISHED)
    return None if ok else f"record status is {r.status.value}"


def _approve_decision(r: PostRecord, s: Settings) -> str | None:
    return None if r.approval.decision == Decision.APPROVE else "the card holds no approve decision"


def _finalised(r: PostRecord, s: Settings) -> str | None:
    return None if r.approval.decided_at is not None else "the card was never finalised"


def _opening_line(r: PostRecord, s: Settings) -> str | None:
    return None if r.human_contribution.text.strip() else "no opening line from the approver"


def _authorised_approver(r: PostRecord, s: Settings) -> str | None:
    who = r.approval.reviewer
    return None if who in s.approvers else f"{who} is not an authorised approver (D5)"


def _stage_one(r: PostRecord, s: Settings) -> str | None:
    if r.channel_id != Channel.C:
        return None
    so = r.approval.stage_one
    if so is None or so.decision != Decision.APPROVE or so.reviewer not in s.arabic_reviewers:
        return "channel C needs a stage-one approval by an authorised Arabic reviewer (D19)"
    return None


def _gates(r: PostRecord, s: Settings) -> str | None:
    if gates_cleared(r):
        return None
    return (
        "a gate did not pass and was not a question settled on the card; R-20 forbids "
        "any other override"
    )


def _sponsor_permit(r: PostRecord, s: Settings) -> str | None:
    if r.sponsor is not None and r.sponsor.permit_status != "granted":
        return "sponsored post without the UAE permit (PRD 4.6)"
    return None


# in order: the first that fails is the reason given
_APPROVAL_CHECKS = (
    _not_canary,
    _approved_status,
    _approve_decision,
    _finalised,
    _opening_line,
    _authorised_approver,
    _stage_one,
    _gates,
    _sponsor_permit,
)


def approval_refusal(record: PostRecord, settings: Settings) -> str | None:
    """The first reason the approval does not hold, or None when it does."""
    for check in _APPROVAL_CHECKS:
        why = check(record, settings)
        if why:
            return why
    return None
