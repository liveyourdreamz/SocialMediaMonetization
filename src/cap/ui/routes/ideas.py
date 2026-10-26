"""This week's ideas on the Digest (D48, ADR-015): pick, edit or reject each one.

Only a pick does anything: it files the brief exactly as the Operator form does (the
brief builder's rules apply, D47) and queues its first job. Rejecting only records the
decision; an idea nobody touches expires the next Monday and is remembered 90 days.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse, Response

from cap.approval.identity import Reviewer, check_csrf, current_reviewer
from cap.ideabook import IdeaDecidedError, IdeaNotFoundError
from cap.schema.ideas import IdeaStatus
from cap.schema.post_record import Lane
from cap.store import PostStore
from cap.ui.page import app_store, approver_only

router = APIRouter(include_in_schema=False)


def _idea_or_404(store: PostStore, idea_id: str):  # type: ignore[no-untyped-def]
    try:
        return store.ideas.get(idea_id)
    except IdeaNotFoundError:
        raise HTTPException(404, "no such idea") from None


@router.post("/ideas/{idea_id}/pick", dependencies=[Depends(check_csrf)])
def pick(
    idea_id: str,
    topic: str = Form(default=""),
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    from cap.demo.seed import new_brief

    idea = _idea_or_404(store, idea_id)
    if idea.status != IdeaStatus.SHOWN:
        raise HTTPException(409, f"this idea was already {idea.status.value}")
    title = (topic.strip() or idea.topic)[:140]
    record = new_brief(idea.channel, title)
    if idea.risk_lane == "high" and record.brief.lane != Lane.HIGH:
        # P5 judged it sensitive though the word list did not: the higher lane wins
        record.brief.lane = Lane.HIGH
        record.log("high-risk lane: idea scoring judged the topic sensitive")
    record.log(
        f"from this week's ideas (rank {idea.rank}); evidence: {', '.join(idea.evidence_urls)}"
    )
    try:
        store.ideas.decide(idea_id, IdeaStatus.PICKED, topic_edit=topic, post_id=record.post_id)
    except IdeaDecidedError:
        raise HTTPException(409, "this idea was already decided") from None
    from cap.demo.canary_job import file_brief

    payload = {"urls": idea.evidence_urls, "test_log": ""}
    file_brief(store, record, payload, idea_id=idea_id)
    store.audit.append("idea picked", post_id=record.post_id, detail={"idea": idea_id})
    return RedirectResponse("/operator", status_code=303)


@router.post("/operator/ideas", dependencies=[Depends(check_csrf)])
def run_now(
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """Queue this week's trend research for every channel now (the button on Operator)."""
    approver_only(reviewer)
    from cap.config import get_settings
    from cap.demo.ideas_job import queue_now

    for ch in get_settings().channels:
        queue_now(store, ch)
    return RedirectResponse("/operator#ideas", status_code=303)


@router.post("/ideas/{idea_id}/reject", dependencies=[Depends(check_csrf)])
def reject(
    idea_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    _idea_or_404(store, idea_id)
    try:
        store.ideas.decide(idea_id, IdeaStatus.REJECTED)
    except IdeaDecidedError:
        raise HTTPException(409, "this idea was already decided") from None
    store.audit.append("idea rejected", detail={"idea": idea_id})
    return RedirectResponse("/digest#ideas", status_code=303)
