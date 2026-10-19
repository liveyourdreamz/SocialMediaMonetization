"""Approval queue API (PRD 4.5, D14). FastAPI; the phone-friendly front end calls
these endpoints. Google sign-in and the two roles (D19) are added when the Cloud
project exists; Foundation uses a header-based reviewer identity behind a local-only
guard so the flow can be exercised end to end.

Endpoints
  GET  /queue                       cards awaiting approval (blind view)
  GET  /posts/{id}/card             one card
  POST /posts/{id}/verdict          first verdict (D33 pre-reveal) -> revealed gates
  POST /posts/{id}/finalise         optional revision, then token issue on approve
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from cap.approval import token as tok
from cap.approval.card import CardError, build_card, finalise, record_verdict
from cap.config import get_settings
from cap.schema.post_record import Decision, PostRecord, PostStatus, ReasonCode


class Store:
    """In-memory store for Foundation; the Cloud SQL repository replaces it with the
    same four methods."""

    def __init__(self) -> None:
        self.posts: dict[str, PostRecord] = {}

    def get(self, post_id: str) -> PostRecord:
        try:
            return self.posts[post_id]
        except KeyError as e:
            raise HTTPException(404, "post not found") from e

    def put(self, record: PostRecord) -> None:
        self.posts[record.post_id] = record

    def queue(self) -> list[PostRecord]:
        return [p for p in self.posts.values() if p.status == PostStatus.AWAITING_APPROVAL]


def reviewer_identity(x_reviewer: str | None = Header(default=None)) -> str:
    """Foundation stand-in for Google sign-in. Refuses outside local."""
    if get_settings().env != "local":
        raise HTTPException(503, "reviewer sign-in not configured for this environment")
    if not x_reviewer:
        raise HTTPException(401, "X-Reviewer header required")
    return x_reviewer


class VerdictIn(BaseModel):
    decision: Decision
    time_on_card_s: int
    reason_code: ReasonCode | None = None
    opening_line: str | None = None
    spot_check_supported: bool | None = None


class FinaliseIn(BaseModel):
    revised_decision: Decision | None = None
    reason_code: ReasonCode | None = None


def create_app(store: Store | None = None) -> FastAPI:
    store = store or Store()
    app = FastAPI(title="Content Agent Portfolio approval queue")
    app.state.store = store

    @app.get("/queue")
    def queue(reviewer: str = Depends(reviewer_identity)) -> list[dict[str, Any]]:
        return [asdict(build_card(p)) for p in store.queue()]

    @app.get("/posts/{post_id}/card")
    def card(post_id: str, reviewer: str = Depends(reviewer_identity)) -> dict[str, Any]:
        return asdict(build_card(store.get(post_id)))

    @app.post("/posts/{post_id}/verdict")
    def verdict(
        post_id: str, body: VerdictIn, reviewer: str = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        record = store.get(post_id)
        try:
            record_verdict(
                record,
                body.decision,
                reviewer=reviewer,
                time_on_card_s=body.time_on_card_s,
                reason_code=body.reason_code,
                opening_line=body.opening_line,
                spot_check_supported=body.spot_check_supported,
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        store.put(record)
        return asdict(build_card(record))  # gates now revealed (D33)

    @app.post("/posts/{post_id}/finalise")
    def finalise_post(
        post_id: str, body: FinaliseIn, reviewer: str = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        record = store.get(post_id)
        try:
            finalise(record, revised_decision=body.revised_decision, reason_code=body.reason_code)
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        token_str = ""
        if record.status == PostStatus.APPROVED:
            t = tok.issue(
                record.post_id, record.asset.sha256, reviewer, "approve", canary=record.flags.canary
            )
            record.approval.token = t.encode()
            token_str = record.approval.token
        store.put(record)
        return {
            "post_id": record.post_id,
            "status": record.status.value,
            "token_issued": bool(token_str),
        }

    return app
