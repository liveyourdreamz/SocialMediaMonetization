"""The guided tour: the worked example, stop by stop."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cap.approval.identity import (
    Reviewer,
    check_csrf,
    current_reviewer,
)
from cap.store import PostStore
from cap.ui import tour as tour_view
from cap.ui import views
from cap.ui.page import app_store, approver_only, render

router = APIRouter(include_in_schema=False)


# ---- guided tour: one worked Patch episode, idea to published ----------------
@router.get("/tour", response_class=HTMLResponse)
def tour_intro(
    request: Request,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    current = [p for p in store.all() if p.flags.tour]
    current.sort(key=lambda p: p.created_at, reverse=True)
    return render(
        request,
        "tour_intro.html",
        reviewer,
        stops=tour_view.STOPS,
        current=current[0] if current else None,
    )


@router.post("/tour/start", dependencies=[Depends(check_csrf)])
def tour_start(
    reviewer: Reviewer = Depends(current_reviewer), store: PostStore = Depends(app_store)
) -> Response:
    approver_only(reviewer)
    from cap.demo.seed import begin_tour

    rec = begin_tour(store)
    return RedirectResponse(f"/tour/{rec.post_id}/1", status_code=303)


@router.get("/tour/{post_id}/{n}", response_class=HTMLResponse)
def tour_stop(
    request: Request,
    post_id: str,
    n: int,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    record = store.get(post_id)
    if not record.flags.tour:
        raise HTTPException(404, "not a tour post")
    pkg = views.package(record) if record.approval.token else None
    from cap import connections as conn

    return render(
        request,
        "tour.html",
        reviewer,
        record=record,
        stops=tour_view.STOPS,
        platforms=[conn.status(k) for k in conn.PLATFORMS],
        results=views.publish_results(record),
        local_dir=str((store.local_published_dir() / record.post_id).resolve()),
        **tour_view.context(record, n, pkg),
    )
