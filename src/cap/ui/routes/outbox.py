"""The Outbox: the final release, the publish button (D5: the token is checked again by the publisher), retry and re-issue, and the media files the pages play."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from cap import release
from cap.approval import release_job
from cap.approval.app import publish_now, visible_to
from cap.approval.identity import (
    Reviewer,
    check_csrf,
    current_reviewer,
)
from cap.publisher import PublishRefusedError
from cap.store import PostStore
from cap.ui import views
from cap.ui.page import app_store, approver_only, render

router = APIRouter(include_in_schema=False)


# ---- the outbox: what goes out, and the publish step (PRD 4.6)
@router.get("/posts/{post_id}/preview", response_class=HTMLResponse)
def preview(
    request: Request,
    post_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    record = store.get(post_id)
    from cap import connections as conn

    available = conn.connected_destinations()
    return render(
        request,
        "preview.html",
        reviewer,
        record=record,
        pkg=views.package(record),
        destinations=[(k, label, k in available) for k, label in conn.DESTINATION_LABELS.items()],
        any_connected=len(available) > 1,
        results=views.publish_results(record),
        release=views.release_view(record),
        cost=views.cost_view(record),
        flags=release.flags_for(record),
        local_dir=str((store.local_published_dir() / record.post_id).resolve()),
        content_rtl=views.is_rtl(record.channel_id.value),
        channel_label=views.channel_label(record.channel_id.value),
        refusal=request.query_params.get("refused", ""),
    )


@router.post("/posts/{post_id}/publish", dependencies=[Depends(check_csrf)])
async def publish_post(
    request: Request,
    post_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    if store.paused:
        return RedirectResponse("/paused", status_code=303)
    store.get(post_id)  # 404 for an unknown post; publish_now reads it again under its lock
    form = await request.form()
    from cap.connections import DESTINATION_LABELS

    chosen = [
        str(d)
        for d in form.getlist("destinations")
        if isinstance(d, str) and d in DESTINATION_LABELS and d != "local"
    ]
    # uploads take minutes: off the event loop, so the app keeps answering (H5)
    refused = await run_in_threadpool(publish_now, store, post_id, chosen)
    return _back_to_outbox(post_id, refused)


def _back_to_outbox(post_id: str, refused: str | None) -> Response:
    if refused:
        from urllib.parse import quote

        return RedirectResponse(
            f"/posts/{post_id}/preview?refused={quote(refused)}", status_code=303
        )
    return RedirectResponse(f"/posts/{post_id}/preview", status_code=303)


@router.post("/posts/{post_id}/release/retry", dependencies=[Depends(check_csrf)])
def release_retry(
    post_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """Make the final video again after a failure (or for the first time for a post
    approved before final renders existed)."""
    approver_only(reviewer)
    if store.paused:
        return RedirectResponse("/paused", status_code=303)
    record = store.get(post_id)
    if not release_job.can_start(record):
        return _back_to_outbox(post_id, "the final video is ready or already being made")
    try:
        release_job.begin(store, record)
    except PublishRefusedError as e:
        return _back_to_outbox(post_id, str(e))
    return _back_to_outbox(post_id, None)


@router.post("/posts/{post_id}/release/reissue", dependencies=[Depends(check_csrf)])
def release_reissue(
    post_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """M4: a fresh token for an unchanged release (tokens expire after a week)."""
    approver_only(reviewer)
    if store.paused:
        return RedirectResponse("/paused", status_code=303)
    record = store.get(post_id)
    try:
        release.reissue(record, reviewer.name)
    except (PublishRefusedError, PermissionError) as e:
        record.log(f"token re-issue refused: {e}")
        store.put(record)
        return _back_to_outbox(post_id, str(e))
    store.put(record)
    return _back_to_outbox(post_id, None)


def _send(record_files: list[str], name: str) -> Response:
    from fastapi.responses import FileResponse

    path = {Path(p).name: Path(p) for p in record_files if p}.get(name)
    if path is None or not path.exists():
        raise HTTPException(404, "no such rendered file")
    return FileResponse(path)


@router.get("/assets/{post_id}/final/{name}")
def final_asset(
    post_id: str,
    name: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """The final release's files (the opening line spoken): what is published."""
    record = store.get(post_id)
    if not visible_to(record, reviewer):
        raise HTTPException(403, "channel C only")
    return _send([f.uri for f in record.release.files], name)


@router.get("/assets/{post_id}/{name}")
def asset(
    post_id: str,
    name: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """The review render of one post, to the people who may see the post."""
    record = store.get(post_id)
    if not visible_to(record, reviewer):
        raise HTTPException(403, "channel C only")
    a = record.asset
    return _send([a.uri, a.short_uri, a.thumbnail_uri], name)
