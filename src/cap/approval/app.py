"""The approval app (PRD 4.5, D14, D39): the HTML screens the reviewers use and the
JSON API underneath them.

HTML (cap.ui): /  Portfolio · /queue · /posts/{id} · /posts/{id}/approve · /paused ·
/digest · /operator · /login (local stand-in for Google sign-in).

JSON, under /api, same rules, same store:
  GET  /api/queue                 cards awaiting approval (blind view)
  GET  /api/posts/{id}/card       one card
  POST /api/posts/{id}/stage-one  Arabic reviewer's pass (channel C, D19)
  POST /api/posts/{id}/verdict    first verdict (D33 pre-reveal) -> revealed gates
  POST /api/posts/{id}/finalise   optional single revision, then token issue on approve

Identity: cap.approval.identity (header or signed cookie, opt-in, local only).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from cap.approval import token as tok
from cap.approval.card import (
    CanaryMissedError,
    CardError,
    build_card,
    finalise,
    record_stage_one,
    record_verdict,
)
from cap.approval.identity import reviewer_identity
from cap.schema.post_record import Decision, PostRecord, PostStatus, ReasonCode


class Store:
    """In-memory store for Foundation; the Cloud SQL repository replaces it with the
    same methods. `opened_at` is the server's clock on a card (D34: time on card is
    measured here, never trusted from the browser). `analytics` is what agent 14
    fills; empty until it runs, and the Portfolio says so."""

    def __init__(self) -> None:
        self.posts: dict[str, PostRecord] = {}
        self.paused: bool = False  # P14: a missed canary pauses the session
        self.paused_reason: str = ""
        self.opened_at: dict[tuple[str, str], datetime] = {}
        self.analytics: dict[str, dict[str, Any]] = {}  # channel id -> figures + source
        self.analytics_note: str = ""  # "example figures" in demo mode; else empty

    def get(self, post_id: str) -> PostRecord:
        try:
            return self.posts[post_id]
        except KeyError as e:
            raise HTTPException(404, "post not found") from e

    def put(self, record: PostRecord) -> None:
        self.posts[record.post_id] = record

    def queue(self) -> list[PostRecord]:
        waiting = [p for p in self.posts.values() if p.status == PostStatus.AWAITING_APPROVAL]
        return sorted(waiting, key=lambda p: p.created_at)  # oldest first, the only priority

    def open_card(self, post_id: str, reviewer: str) -> int:
        """First open starts the clock; later opens return seconds elapsed so far."""
        key = (post_id, reviewer)
        now = datetime.now(UTC)
        if key not in self.opened_at:
            self.opened_at[key] = now
            return 0
        return int((now - self.opened_at[key]).total_seconds())

    def seconds_on_card(self, post_id: str, reviewer: str) -> int:
        at = self.opened_at.get((post_id, reviewer))
        return int((datetime.now(UTC) - at).total_seconds()) if at else 0

    def pause(self, reason: str) -> None:
        self.paused, self.paused_reason = True, reason

    def reopen(self) -> None:
        self.paused, self.paused_reason = False, ""


class StageOneIn(BaseModel):
    decision: Decision
    notes: str = ""
    time_on_card_s: int


class VerdictIn(BaseModel):
    decision: Decision
    time_on_card_s: int
    reason_code: ReasonCode | None = None
    opening_line: str | None = None
    spot_check_supported: bool | None = None


class FinaliseIn(BaseModel):
    revised_decision: Decision | None = None
    reason_code: ReasonCode | None = None
    opening_line: str | None = None
    spot_check_supported: bool | None = None


def finalise_and_issue(store: Store, record: PostRecord, reviewer: str, **kw: Any) -> bool:
    """Shared by the API and the HTML app: finalise, pause on a missed canary, issue
    the token on approve. Returns whether a token was issued. Raises CardError."""
    try:
        finalise(record, reviewer=reviewer, **kw)
    except CanaryMissedError:
        store.pause(f"canary missed on {record.post_id} by {reviewer}")
        store.put(record)
        return False
    issued = False
    if record.status == PostStatus.APPROVED:
        t = tok.issue(record.post_id, record.asset.sha256, reviewer, "approve", canary=False)
        record.approval.token = t.encode()
        issued = True
    store.put(record)
    return issued


def api_router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/api")

    def _guard_paused() -> None:
        if store.paused:
            raise HTTPException(423, "session paused after a missed canary; operator review needed")

    @r.get("/queue")
    def queue(reviewer: str = Depends(reviewer_identity)) -> list[dict[str, Any]]:
        return [asdict(build_card(p)) for p in store.queue()]

    @r.get("/posts/{post_id}/card")
    def card(post_id: str, reviewer: str = Depends(reviewer_identity)) -> dict[str, Any]:
        return asdict(build_card(store.get(post_id)))

    @r.post("/posts/{post_id}/stage-one")
    def stage_one(
        post_id: str, body: StageOneIn, reviewer: str = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        record = store.get(post_id)
        try:
            record_stage_one(
                record,
                reviewer=reviewer,
                decision=body.decision,
                notes=body.notes,
                time_on_card_s=body.time_on_card_s,
            )
        except CardError as e:
            raise HTTPException(422, str(e)) from e
        store.put(record)
        return {"post_id": record.post_id, "stage_one": body.decision.value}

    @r.post("/posts/{post_id}/verdict")
    def verdict(
        post_id: str, body: VerdictIn, reviewer: str = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
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

    @r.post("/posts/{post_id}/finalise")
    def finalise_post(
        post_id: str, body: FinaliseIn, reviewer: str = Depends(reviewer_identity)
    ) -> dict[str, Any]:
        _guard_paused()
        record = store.get(post_id)
        try:
            issued = finalise_and_issue(
                store,
                record,
                reviewer,
                revised_decision=body.revised_decision,
                reason_code=body.reason_code,
                opening_line=body.opening_line,
                spot_check_supported=body.spot_check_supported,
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
            "token_issued": issued,
        }
        if record.flags.canary:
            out["canary"] = record.flags.canary_outcome
        return out

    return r


def create_app(store: Store | None = None) -> FastAPI:
    from cap.ui import (
        STATIC,
        ui_router,
    )  # the screens; imported here to keep the API importable alone

    store = store or Store()
    app = FastAPI(
        title="Content Agent Portfolio", docs_url="/api/docs", openapi_url="/api/openapi.json"
    )
    app.state.store = store
    app.include_router(api_router(store))
    app.include_router(ui_router(store))
    app.mount("/static", STATIC, name="static")
    return app
