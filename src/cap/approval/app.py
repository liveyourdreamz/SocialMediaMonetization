"""The approval steps the screens (cap.ui) and the JSON API (cap.approval.api) share
(PRD 4.5, D14, D39): finalising a verdict and queueing the final render, publishing,
and what each reviewer may see (D19).

Identity: cap.approval.identity (a server-held session; the X-Reviewer header only
under the test-only setting). Host headers: cap.approval.hosts.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel

from cap.approval import release_job
from cap.approval.identity import Reviewer
from cap.demo.canary_job import refile_clean
from cap.domain.card import (
    CanaryMissedError,
    CardView,
    build_card,
    finalise,
)
from cap.gates.history import record_approved
from cap.publisher import PlatformAdapter, PublishRefusedError
from cap.schema.post_record import (
    Channel,
    Decision,
    PostRecord,
    PostStatus,
    ReasonCode,
    ReleaseStatus,
)
from cap.store import LockBusyError, PostConflictError, PostStore


class StageOneIn(BaseModel):
    decision: Decision
    notes: str = ""


class VerdictIn(BaseModel):
    decision: Decision
    reason_code: ReasonCode | None = None
    opening_line: str | None = None
    spot_check_supported: bool | None = None


class FinaliseIn(BaseModel):
    revised_decision: Decision | None = None
    reason_code: ReasonCode | None = None
    opening_line: str | None = None
    spot_check_supported: bool | None = None
    acknowledged: bool = False


def finalise_and_issue(store: PostStore, record: PostRecord, reviewer: str, **kw: Any) -> bool:
    """Shared by the API and the HTML app: finalise, pause on a missed canary, and on
    approve start the final render (cap.approval.release_job), which issues the token
    once the final files exist (review C1). Returns whether the final render started.
    Raises CardError."""
    try:
        finalise(record, reviewer=reviewer, **kw)
    except CanaryMissedError:
        store.pause(f"canary missed on {record.post_id} by {reviewer}")
        store.put(record)
        refile_clean(store, record)
        return False
    store.put(record)
    if record.flags.canary:  # caught: the brief it replaced is drafted clean now
        refile_clean(store, record)
        return False
    if record.status != PostStatus.APPROVED:
        return False
    # the originality gate compares new posts with approved ones (review C7)
    record_approved(record, store.history())
    try:
        release_job.begin(store, record)
    except PublishRefusedError as e:  # the approval does not hold for release: say so
        record.release.status = ReleaseStatus.FAILED
        record.release.error = str(e)
        record.log(f"final video not started: {e}")
        store.put(record)
        return False
    return True


def publish_now(
    store: PostStore,
    post_id: str,
    destinations: list[str] | None = None,
    publisher: PlatformAdapter | None = None,
) -> str | None:
    """The plain-code publisher (PRD 4.6) on one approved post; cap.release.authorise
    decides, before every upload, whether it may go. Returns the refusal text, or
    None when the publisher ran (each destination's result is on the record).
    `destinations` are the Outbox's choices (youtube_long, facebook, ...); the local
    folder is always first. A tour post never leaves the laptop. The scheduler calls
    this at the posting slot; the Outbox button calls it now.

    One publish of a post at a time, across every process (the store's lock), and the
    post is read inside it, so a request holding an older copy never sends again what
    another has just sent (review R8)."""
    try:
        with store.exclusive(f"publish:{post_id}", wait=False):
            return _publish_locked(store, store.get(post_id), destinations, publisher)
    except LockBusyError:
        return "a publish of this post is already running"


def _publish_locked(
    store: PostStore,
    record: PostRecord,
    destinations: list[str] | None,
    publisher: PlatformAdapter | None,
) -> str | None:
    from cap.publisher import adapters_for, publish_to
    from cap.release import decode_token

    chosen = [] if record.flags.tour else list(destinations or [])
    if publisher is not None and destinations is None and not record.flags.tour:
        # a programmatic call (the scheduler, the full-run check) names its publisher;
        # the Outbox always passes a list, so the ticked boxes decide there
        adapters: list[PlatformAdapter] = [publisher]
        wanted = [publisher.name]
    else:
        adapters = adapters_for(chosen, store.local_published_dir())
        wanted = ["local", *chosen]
    try:
        record.publish.destinations = wanted
        publish_to(record, decode_token(record), adapters, save=lambda r: _save_publish(store, r))
    except PublishRefusedError as e:
        record.log(f"publish refused: {e}")
        _save_publish(store, record)
        return str(e)
    return None


def _save_publish(store: PostStore, record: PostRecord) -> None:
    """Save what the publisher did. Someone else saving the post in between (a note
    from the worker, say) must never cost a platform id that has already been sent, so
    on a conflict the publish block and status are laid onto the current copy (only
    the publisher writes them, and it holds the publish lock)."""
    try:
        store.put(record)
    except PostConflictError:

        def lay_on(current: PostRecord) -> None:
            current.publish = record.publish.model_copy(deep=True)
            current.status = record.status
            current.log("publish results saved over a concurrent change")

        merged = store.update(record.post_id, lay_on)
        for name in type(record).model_fields:  # carry on from the merged copy
            setattr(record, name, getattr(merged, name))


def visible_to(record: PostRecord, reviewer: Reviewer) -> bool:
    """D19: the Arabic reviewer sees channel C only; approvers see everything."""
    return reviewer.is_approver or record.channel_id == Channel.C


def card_for(record: PostRecord, reviewer: Reviewer) -> CardView:
    """The card as this reviewer may see it. The Arabic reviewer never sees gate
    outcomes, critic notes or planted faults, whatever the record's state (D19)."""
    if not visible_to(record, reviewer):
        raise HTTPException(403, "channel C only")
    view = build_card(record)
    if not reviewer.is_approver:
        view.gates, view.critic_issues, view.planted_fault, view.gates_revealed = (
            None,
            None,
            None,
            False,
        )
    return view
