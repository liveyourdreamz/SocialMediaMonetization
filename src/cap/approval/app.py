"""The approval app (PRD 4.5, D14, D39): the HTML screens the reviewers use and the
JSON API underneath them.

HTML (cap.ui): /  Portfolio · /queue · /posts/{id} · /posts/{id}/approve · /paused ·
/digest · /operator · /login (local stand-in for Google sign-in).

JSON, under /api, same rules, same store:
  GET  /api/queue                 cards awaiting approval (blind view)
  GET  /api/posts/{id}/card       one card
  POST /api/posts/{id}/stage-one  Arabic reviewer's pass (channel C, D19)
  POST /api/posts/{id}/verdict    first verdict (D33 pre-reveal) -> revealed gates
  POST /api/posts/{id}/finalise   optional single revision; on approve the final render
                                  starts, and the token is issued when it is done
  GET  /api/posts/{id}/release    the final release: status, error, token present

Identity: cap.approval.identity (a server-held session; the X-Reviewer header only
under the test-only setting). Host headers: cap.approval.hosts.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from cap.approval import release_job
from cap.approval.identity import Reviewer, reviewer_identity
from cap.domain.card import (
    CanaryMissedError,
    CardError,
    CardView,
    build_card,
    finalise,
    record_stage_one,
    record_verdict,
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
from cap.store import PostStore


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
        return False
    store.put(record)
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
    record: PostRecord,
    destinations: list[str] | None = None,
    publisher: PlatformAdapter | None = None,
) -> str | None:
    """The plain-code publisher (PRD 4.6) on one approved post; cap.release.authorise
    decides, before every upload, whether it may go. Returns the refusal text, or
    None when the publisher ran (each destination's result is on the record).
    `destinations` are the Outbox's choices (youtube_long, facebook, ...); the local
    folder is always first. A tour post never leaves the laptop. The scheduler calls
    this at the posting slot; the Outbox button calls it now."""
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
        publish_to(record, decode_token(record), adapters, save=store.put)
    except PublishRefusedError as e:
        record.log(f"publish refused: {e}")
        store.put(record)
        return str(e)
    return None


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


def api_router(store: PostStore) -> APIRouter:  # noqa: C901 - M1 step 4 splits it
    r = APIRouter(prefix="/api")

    def _guard_paused() -> None:
        if store.paused:
            raise HTTPException(423, "session paused after a missed canary; operator review needed")

    def _approver(reviewer: Reviewer) -> None:
        if not reviewer.is_approver:
            raise HTTPException(403, "approvers only")

    @r.get("/queue")
    def queue(reviewer: Reviewer = Depends(reviewer_identity)) -> list[dict[str, Any]]:
        return [asdict(card_for(p, reviewer)) for p in store.queue() if visible_to(p, reviewer)]

    @r.get("/posts/{post_id}/card")
    def card(post_id: str, reviewer: Reviewer = Depends(reviewer_identity)) -> dict[str, Any]:
        record = store.get(post_id)
        view = card_for(record, reviewer)
        if record.status == PostStatus.AWAITING_APPROVAL:
            store.open_card(post_id, reviewer.name)  # the clock starts here, server-side
        return asdict(view)

    @r.post("/posts/{post_id}/stage-one")
    def stage_one(
        post_id: str, body: StageOneIn, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        if reviewer.role != "arabic":
            raise HTTPException(403, "the Arabic reviewer records stage one")
        record = store.get(post_id)
        try:
            record_stage_one(
                record,
                reviewer=reviewer.name,
                decision=body.decision,
                notes=body.notes,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        store.put(record)
        return {"post_id": record.post_id, "stage_one": body.decision.value}

    @r.post("/posts/{post_id}/verdict")
    def verdict(
        post_id: str, body: VerdictIn, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        _approver(reviewer)
        record = store.get(post_id)
        try:
            record_verdict(
                record,
                body.decision,
                reviewer=reviewer.name,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
                reason_code=body.reason_code,
                opening_line=body.opening_line,
                spot_check_supported=body.spot_check_supported,
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        store.put(record)
        return asdict(build_card(record))  # gates now revealed (D33)

    @r.post("/posts/{post_id}/finalise")
    def finalise_post(
        post_id: str, body: FinaliseIn, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        _approver(reviewer)
        record = store.get(post_id)
        try:
            started = finalise_and_issue(
                store,
                record,
                reviewer.name,
                revised_decision=body.revised_decision,
                reason_code=body.reason_code,
                opening_line=body.opening_line,
                spot_check_supported=body.spot_check_supported,
                acknowledged=body.acknowledged,
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        if store.paused and record.flags.canary_outcome == "missed":
            return {
                "post_id": record.post_id,
                "status": record.status.value,
                "canary": "missed",
                "session_paused": True,
            }
        out: dict[str, Any] = {
            "post_id": record.post_id,
            "status": record.status.value,
            # the final render runs in the background; GET .../release says when the
            # token is there
            "final_render_started": started,
        }
        if record.flags.canary:
            out["canary"] = record.flags.canary_outcome
        return out

    @r.get("/posts/{post_id}/release")
    def release_state(
        post_id: str, reviewer: Reviewer = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _approver(reviewer)
        record = store.get(post_id)
        return {
            "post_id": record.post_id,
            "status": record.status.value,
            "release": record.release.status.value,
            "error": record.release.error,
            "token_issued": bool(record.approval.token),
        }

    return r
