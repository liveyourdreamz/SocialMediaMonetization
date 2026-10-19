"""The screens (D38, D39), rendered server-side on the Proof design system.

Every rule the card enforces lives in cap.approval.card; these routes only collect
the reviewer's inputs and show what the card functions return. A screen can never
show what the API would refuse: the blind card is built by build_card(), which
drops the gate fields until the verdict is recorded. Time on card is the server's
clock (Store.open_card), never a number the browser sends.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cap.approval.app import Store, finalise_and_issue
from cap.approval.card import CardError, record_stage_one, record_verdict
from cap.approval.identity import (
    COOKIE,
    COOKIE_MAX_AGE_S,
    Reviewer,
    current_reviewer,
    role_for,
    sign_cookie,
)
from cap.config import get_settings
from cap.schema.post_record import Decision, PostStatus, ReasonCode
from cap.ui import views

HERE = Path(__file__).resolve().parent
STATIC = StaticFiles(directory=str(HERE / "static"))
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["minutes"] = views.minutes


def _ctx(request: Request, store: Store, reviewer: Reviewer | None, **extra: Any) -> dict[str, Any]:
    return {
        "request": request,
        "reviewer": reviewer,
        "paused": store.paused,
        "queue_count": len(store.queue()) if reviewer and reviewer.is_approver else 0,
        "settings": get_settings(),
        **extra,
    }


def ui_router(store: Store) -> APIRouter:
    r = APIRouter(include_in_schema=False)

    def render(
        request: Request, template: str, reviewer: Reviewer | None, status: int = 200, **ctx: Any
    ) -> Response:
        return templates.TemplateResponse(
            request, template, _ctx(request, store, reviewer, **ctx), status_code=status
        )

    def _approver(reviewer: Reviewer) -> None:
        if not reviewer.is_approver:
            raise HTTPException(403, "approvers only")

    # ---- sign-in stand-in (local only; Google sign-in sets the same cookie later)
    @r.get("/login", response_class=HTMLResponse)
    def login(request: Request, name: str = "") -> Response:
        if not get_settings().dev_reviewer_header:
            raise HTTPException(503, "sign-in not configured for this environment")
        if name:
            if role_for(name) is None:
                return render(
                    request,
                    "login.html",
                    None,
                    403,
                    error=f"{name} is not on the approver or Arabic-reviewer lists",
                    name=name,
                )
            resp = RedirectResponse("/", status_code=303)
            resp.set_cookie(
                COOKIE, sign_cookie(name), max_age=COOKIE_MAX_AGE_S, httponly=True, samesite="lax"
            )
            return resp
        return render(request, "login.html", None, name="", error="")

    @r.post("/logout")
    def logout() -> Response:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        return resp

    # ---- landing
    @r.get("/", response_class=HTMLResponse)
    def home(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        if not reviewer.is_approver:
            return RedirectResponse("/queue", status_code=303)
        return render(
            request, "portfolio.html", reviewer, tab="portfolio", p=views.portfolio(store)
        )

    @r.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        items = views.queue_items(store.queue(), reviewer.role)
        return render(
            request, "queue.html", reviewer, tab="queue", items=items, rtl=not reviewer.is_approver
        )

    # ---- the card
    @r.get("/posts/{post_id}", response_class=HTMLResponse)
    def card(
        request: Request, post_id: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        record = store.get(post_id)
        if not reviewer.is_approver and not views.is_rtl(record.channel_id.value):
            raise HTTPException(403, "channel C only")
        if (
            record.status == PostStatus.AWAITING_APPROVAL
            and record.approval.pre_reveal_decision is None
        ):
            store.open_card(post_id, reviewer.name)
        ctx = views.card_context(
            record, store, reviewer.name, "ar" if not reviewer.is_approver else "en"
        )
        if not reviewer.is_approver:
            return render(request, "stage_one.html", reviewer, **ctx)
        return render(request, "card.html", reviewer, **ctx)

    @r.get("/posts/{post_id}/approve", response_class=HTMLResponse)
    def approve_form(
        request: Request, post_id: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        _approver(reviewer)
        record = store.get(post_id)
        ctx = views.card_context(record, store, reviewer.name)
        if ctx["needs_stage_one"]:
            return render(
                request,
                "card.html",
                reviewer,
                error="Channel C needs the Arabic reviewer's stage-one pass first (D19).",
                **ctx,
            )
        return render(request, "approve.html", reviewer, **ctx)

    @r.post("/posts/{post_id}/verdict")
    def verdict(
        request: Request,
        post_id: str,
        decision: str = Form(...),
        reason_code: str | None = Form(default=None),
        opening_line: str | None = Form(default=None),
        spot_check_supported: str | None = Form(default=None),
        note: str = Form(default=""),
        reviewer: Reviewer = Depends(current_reviewer),
    ) -> Response:
        _approver(reviewer)
        if store.paused:
            return RedirectResponse("/paused", status_code=303)
        record = store.get(post_id)
        spot = None if spot_check_supported in (None, "") else spot_check_supported == "yes"
        try:
            record_verdict(
                record,
                Decision(decision),
                reviewer=reviewer.name,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
                reason_code=ReasonCode(reason_code) if reason_code else None,
                opening_line=opening_line,
                spot_check_supported=spot,
            )
        except CardError as e:
            ctx = views.card_context(record, store, reviewer.name)
            page = "approve.html" if decision == "approve" else "card.html"
            return render(request, page, reviewer, 422, error=str(e), **ctx)
        if note.strip():
            record.approval.edits.append(f"note: {note.strip()}")
        store.put(record)
        return RedirectResponse(f"/posts/{post_id}", status_code=303)

    @r.post("/posts/{post_id}/finalise")
    def finalise_post(
        request: Request,
        post_id: str,
        revised_decision: str | None = Form(default=None),
        reason_code: str | None = Form(default=None),
        opening_line: str | None = Form(default=None),
        spot_check_supported: str | None = Form(default=None),
        reviewer: Reviewer = Depends(current_reviewer),
    ) -> Response:
        _approver(reviewer)
        if store.paused:
            return RedirectResponse("/paused", status_code=303)
        record = store.get(post_id)
        spot = None if spot_check_supported in (None, "") else spot_check_supported == "yes"
        try:
            finalise_and_issue(
                store,
                record,
                reviewer.name,
                revised_decision=Decision(revised_decision) if revised_decision else None,
                reason_code=ReasonCode(reason_code) if reason_code else None,
                opening_line=opening_line,
                spot_check_supported=spot,
            )
        except CardError as e:
            ctx = views.card_context(record, store, reviewer.name)
            return render(request, "card.html", reviewer, 422, error=str(e), **ctx)
        if store.paused:
            return RedirectResponse("/paused", status_code=303)
        return RedirectResponse("/queue", status_code=303)

    # ---- channel C, stage one (D19)
    @r.post("/posts/{post_id}/stage-one")
    def stage_one(
        request: Request,
        post_id: str,
        decision: str = Form(...),
        notes: str = Form(default=""),
        reviewer: Reviewer = Depends(current_reviewer),
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
                decision=Decision(decision),
                notes=notes,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
            )
        except CardError as e:
            ctx = views.card_context(record, store, reviewer.name, "ar")
            return render(request, "stage_one.html", reviewer, 422, error=str(e), **ctx)
        store.put(record)
        return RedirectResponse("/queue", status_code=303)

    # ---- paused queue (P14)
    @r.get("/paused", response_class=HTMLResponse)
    def paused(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        missed = [p for p in store.posts.values() if p.flags.canary_outcome == "missed"]
        last = max(missed, key=lambda p: p.created_at) if missed else None
        caught, miss = views.canary_record(store)
        return render(
            request,
            "paused.html",
            reviewer,
            last=last,
            last_reason=views.REASON_TEXT.get(last.approval.reason_code)
            if last and last.approval.reason_code
            else None,
            planted=views.REASON_TEXT.get(ReasonCode(last.flags.canary_fault_type.value))
            if last and last.flags.canary_fault_type
            else None,
            caught=caught,
            missed=miss,
            rate=views.canary_rate(store),
        )

    @r.post("/paused/reopen")
    def reopen(reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        _approver(reviewer)
        store.reopen()
        return RedirectResponse("/queue", status_code=303)

    # ---- digest and operator
    @r.get("/digest", response_class=HTMLResponse)
    def digest(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        _approver(reviewer)
        return render(request, "digest.html", reviewer, tab="digest", d=views.digest(store))

    @r.get("/operator", response_class=HTMLResponse)
    def operator(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        _approver(reviewer)
        return render(
            request,
            "operator.html",
            reviewer,
            tab="operator",
            parked=views.parked(store),
            queue_state=store,
        )

    return r
