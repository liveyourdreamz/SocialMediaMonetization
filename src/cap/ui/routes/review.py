"""The review screens: Portfolio, queue, the card (blind first, D33), the verdict, finalise, the Arabic stage one (D19), the canary pause and the digest."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cap.approval.app import finalise_and_issue, visible_to
from cap.approval.identity import (
    Reviewer,
    check_csrf,
    current_reviewer,
)
from cap.domain.card import CardError, record_stage_one, record_verdict
from cap.schema.post_record import Decision, PostStatus, ReasonCode
from cap.store import PostStore
from cap.ui import views
from cap.ui.page import app_store, approver_only, enum_value, render, spot

router = APIRouter(include_in_schema=False)


# ---- landing
@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    if not reviewer.is_approver:
        return RedirectResponse("/queue", status_code=303)
    return render(request, "portfolio.html", reviewer, tab="portfolio", p=views.portfolio(store))


@router.get("/queue", response_class=HTMLResponse)
def queue(
    request: Request,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    lang = "en" if reviewer.is_approver else "ar"
    items = views.queue_items(store.queue(), reviewer.role, lang)
    return render(
        request,
        "queue.html",
        reviewer,
        tab="queue",
        items=items,
        rtl=lang == "ar",
        L=views.LABELS[lang],
    )


# ---- the card
@router.get("/posts/{post_id}", response_class=HTMLResponse)
def card(
    request: Request,
    post_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    sec_purpose: str | None = Header(default=None),
    purpose: str | None = Header(default=None),
    store: PostStore = Depends(app_store),
) -> Response:
    record = store.get(post_id)
    if not visible_to(record, reviewer):
        raise HTTPException(403, "channel C only")
    if not record.flags.card_prepared:
        # still in the pipeline, or parked before it reached the queue: no card yet
        running = record.status.value in ("drafting", "gated", "rendering")
        return render(
            request,
            "progress.html",
            reviewer,
            record=record,
            running=running,
            channel_label=views.channel_label(record.channel_id.value),
            lines=[
                h.split(" ", 1)
                for h in record.history
                if reviewer.is_approver
                or not any(k in h for k in ("gate", "ethics", "facts", "critic", "judge"))
            ],
        )
    prefetch = "prefetch" in (sec_purpose or "") or "prefetch" in (purpose or "")
    if (
        record.status == PostStatus.AWAITING_APPROVAL
        and record.approval.pre_reveal_decision is None
        and not prefetch
    ):
        store.open_card(post_id, reviewer.name)  # the clock starts on the first real open
    lang = "en" if reviewer.is_approver else "ar"
    ctx = views.card_context(record, store, reviewer, lang)
    if not reviewer.is_approver:
        return render(request, "stage_one.html", reviewer, **ctx)
    return render(request, "card.html", reviewer, **ctx)


@router.get("/posts/{post_id}/approve", response_class=HTMLResponse)
def approve_form(
    request: Request,
    post_id: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    record = store.get(post_id)
    ctx = views.card_context(record, store, reviewer)
    if ctx["needs_stage_one"]:
        return render(
            request,
            "card.html",
            reviewer,
            error="Channel C needs the Arabic reviewer's stage-one pass first (D19).",
            **ctx,
        )
    return render(request, "approve.html", reviewer, **ctx)


@router.post("/posts/{post_id}/verdict", dependencies=[Depends(check_csrf)])
def verdict(
    request: Request,
    post_id: str,
    decision: str = Form(...),
    reason_code: str | None = Form(default=None),
    opening_line: str | None = Form(default=None),
    spot_check_supported: str | None = Form(default=None),
    note: str = Form(default=""),
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    if store.paused:
        return RedirectResponse("/paused", status_code=303)
    record = store.get(post_id)
    dec = enum_value(Decision, decision, "decision")
    chip = enum_value(ReasonCode, reason_code, "reason")
    try:
        record_verdict(
            record,
            dec,
            reviewer=reviewer.name,
            time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
            reason_code=chip,
            opening_line=opening_line,
            spot_check_supported=spot(spot_check_supported),
        )
    except CardError as e:
        ctx = views.card_context(record, store, reviewer)
        page = "approve.html" if dec == Decision.APPROVE else "card.html"
        return render(request, page, reviewer, 422, error=str(e), **ctx)
    if note.strip():
        record.approval.edits.append(f"note: {note.strip()}")
    store.put(record)
    return RedirectResponse(f"/posts/{post_id}", status_code=303)


@router.post("/posts/{post_id}/finalise", dependencies=[Depends(check_csrf)])
def finalise_post(
    request: Request,
    post_id: str,
    revised_decision: str | None = Form(default=None),
    reason_code: str | None = Form(default=None),
    opening_line: str | None = Form(default=None),
    spot_check_supported: str | None = Form(default=None),
    acknowledged: str | None = Form(default=None),
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    if store.paused:
        return RedirectResponse("/paused", status_code=303)
    record = store.get(post_id)
    try:
        finalise_and_issue(
            store,
            record,
            reviewer.name,
            revised_decision=enum_value(Decision, revised_decision, "decision"),
            reason_code=enum_value(ReasonCode, reason_code, "reason"),
            opening_line=opening_line,
            spot_check_supported=spot(spot_check_supported),
            acknowledged=acknowledged == "yes",
        )
    except CardError as e:
        ctx = views.card_context(record, store, reviewer)
        return render(request, "card.html", reviewer, 422, error=str(e), **ctx)
    if store.paused:
        return RedirectResponse("/paused", status_code=303)
    return RedirectResponse("/queue", status_code=303)


# ---- channel C, stage one (D19)
@router.post("/posts/{post_id}/stage-one", dependencies=[Depends(check_csrf)])
def stage_one(
    request: Request,
    post_id: str,
    decision: str = Form(...),
    notes: str = Form(default=""),
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    if reviewer.role != "arabic":
        raise HTTPException(403, "the Arabic reviewer records stage one")
    if store.paused:
        return RedirectResponse("/paused", status_code=303)
    record = store.get(post_id)
    try:
        record_stage_one(
            record,
            reviewer=reviewer.name,
            decision=enum_value(Decision, decision, "decision"),
            notes=notes,
            time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
        )
    except CardError as e:
        ctx = views.card_context(record, store, reviewer, "ar")
        return render(request, "stage_one.html", reviewer, 422, error=str(e), **ctx)
    store.put(record)
    return RedirectResponse("/queue", status_code=303)


# ---- paused queue (P14): the record is the approver's; others see only the state
@router.get("/paused", response_class=HTMLResponse)
def paused(
    request: Request,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    if not reviewer.is_approver:
        return render(
            request, "paused.html", reviewer, rtl=True, last=None, caught=None, missed=None
        )
    missed = [p for p in store.all() if p.flags.canary_outcome == "missed"]
    last = max(missed, key=lambda p: p.approval.decided_at or p.created_at) if missed else None
    caught, miss = views.canary_record(store)
    chip = last.approval.pre_reveal_reason_code if last else None
    fault = last.flags.canary_fault_type if last else None
    return render(
        request,
        "paused.html",
        reviewer,
        last=last,
        last_reason=views.REASON_TEXT.get(chip) if chip else None,
        planted=views.REASON_TEXT.get(ReasonCode(fault.value)) if fault else None,
        caught=caught,
        missed=miss,
        rate=views.canary_rate(store),
    )


@router.post("/paused/reopen", dependencies=[Depends(check_csrf)])
def reopen(
    reviewer: Reviewer = Depends(current_reviewer), store: PostStore = Depends(app_store)
) -> Response:
    approver_only(reviewer)
    store.reopen()
    return RedirectResponse("/queue", status_code=303)


# ---- digest and operator
@router.get("/digest", response_class=HTMLResponse)
def digest(
    request: Request,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    return render(request, "digest.html", reviewer, tab="digest", d=views.digest(store))
