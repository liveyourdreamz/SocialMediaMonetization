"""The JSON API under /api (PRD 4.5): the same rules as the screens, the same store.

GET  /api/queue                 cards awaiting approval (blind view)
GET  /api/posts/{id}/card       one card
POST /api/posts/{id}/stage-one  Arabic reviewer's pass (channel C, D19)
POST /api/posts/{id}/verdict    first verdict (D33 pre-reveal) -> revealed gates
POST /api/posts/{id}/finalise   optional single revision; on approve the final render
                                is queued, and the token is issued when it is done
GET  /api/posts/{id}/release    the final release: status, error, token present
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from cap.approval.app import (
    FinaliseIn,
    StageOneIn,
    VerdictIn,
    card_for,
    finalise_and_issue,
    visible_to,
)
from cap.approval.deps import app_store
from cap.approval.identity import Reviewer, reviewer_identity
from cap.domain.card import CardError, build_card, record_stage_one, record_verdict
from cap.schema.post_record import PostStatus
from cap.store import PostStore

router = APIRouter(prefix="/api")


def _guard_paused(store: PostStore) -> None:
    if store.paused:
        raise HTTPException(423, "session paused after a missed canary; operator review needed")


def _approver(reviewer: Reviewer) -> None:
    if not reviewer.is_approver:
        raise HTTPException(403, "approvers only")


@router.get("/queue")
def queue(
    reviewer: Reviewer = Depends(reviewer_identity), store: PostStore = Depends(app_store)
) -> list[dict[str, Any]]:
    return [asdict(card_for(p, reviewer)) for p in store.queue() if visible_to(p, reviewer)]


@router.get("/posts/{post_id}/card")
def card(
    post_id: str,
    reviewer: Reviewer = Depends(reviewer_identity),
    store: PostStore = Depends(app_store),
) -> dict[str, Any]:
    record = store.get(post_id)
    view = card_for(record, reviewer)
    if record.status == PostStatus.AWAITING_APPROVAL:
        store.open_card(post_id, reviewer.name)  # the clock starts here, server-side
    return asdict(view)


@router.post("/posts/{post_id}/stage-one")
def stage_one(
    post_id: str,
    body: StageOneIn,
    reviewer: Reviewer = Depends(reviewer_identity),
    store: PostStore = Depends(app_store),
) -> dict[str, Any]:
    _guard_paused(store)
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


@router.post("/posts/{post_id}/verdict")
def verdict(
    post_id: str,
    body: VerdictIn,
    reviewer: Reviewer = Depends(reviewer_identity),
    store: PostStore = Depends(app_store),
) -> dict[str, Any]:
    _guard_paused(store)
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


@router.post("/posts/{post_id}/finalise")
def finalise_post(
    post_id: str,
    body: FinaliseIn,
    reviewer: Reviewer = Depends(reviewer_identity),
    store: PostStore = Depends(app_store),
) -> dict[str, Any]:
    _guard_paused(store)
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


@router.get("/posts/{post_id}/release")
def release_state(
    post_id: str,
    reviewer: Reviewer = Depends(reviewer_identity),
    store: PostStore = Depends(app_store),
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
