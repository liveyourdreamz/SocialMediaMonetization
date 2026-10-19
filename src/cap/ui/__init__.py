"""The screens (D38, D39), rendered server-side on the Proof design system.

Every rule the card enforces lives in cap.approval.card; these routes only collect
the reviewer's inputs and show what the card functions return. A screen can never
show what the API would refuse: the blind card is built by build_card(), which
drops the gate fields until the verdict is recorded, and card_for() drops them for
the Arabic reviewer always. Time on card is the server's clock (Store.open_card),
never a number the browser sends. Every POST carries a CSRF token.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cap.approval.app import Store, finalise_and_issue, visible_to
from cap.approval.card import CardError, record_stage_one, record_verdict
from cap.approval.identity import (
    COOKIE,
    COOKIE_MAX_AGE_S,
    CSRF_COOKIE,
    Reviewer,
    check_csrf,
    csrf_token,
    current_reviewer,
    new_csrf_token,
    role_for,
    secure_cookies,
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
        "csrf": csrf_token(request),
        **extra,
    }


def _enum(kind: type, value: str | None, what: str) -> Any:
    if value in (None, ""):
        return None
    try:
        return kind(value)
    except ValueError as e:
        raise HTTPException(422, f"unknown {what}: {value!r}") from e


def _spot(value: str | None) -> bool | None:
    return None if value in (None, "") else value == "yes"


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

    # ---- sign-in stand-in (local only; Google sign-in sets the same cookies later)
    @r.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        if not get_settings().dev_reviewer_header:
            raise HTTPException(503, "sign-in not configured for this environment")
        return render(request, "login.html", None, name="", error="")

    @r.post("/login")
    def login(request: Request, name: str = Form(default="")) -> Response:
        if not get_settings().dev_reviewer_header:
            raise HTTPException(503, "sign-in not configured for this environment")
        name = name.strip()
        if not name or role_for(name) is None:
            return render(
                request,
                "login.html",
                None,
                403,
                error="That name is not on a reviewer list.",
                name=name,
            )
        resp = RedirectResponse("/", status_code=303)
        secure = secure_cookies()
        resp.set_cookie(
            COOKIE,
            sign_cookie(name),
            max_age=COOKIE_MAX_AGE_S,
            httponly=True,
            samesite="lax",
            secure=secure,
        )
        resp.set_cookie(
            CSRF_COOKIE,
            new_csrf_token(),
            max_age=COOKIE_MAX_AGE_S,
            httponly=True,
            samesite="lax",
            secure=secure,
        )
        return resp

    @r.post("/logout", dependencies=[Depends(check_csrf)])
    def logout() -> Response:
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(COOKIE)
        resp.delete_cookie(CSRF_COOKIE)
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
    @r.get("/posts/{post_id}", response_class=HTMLResponse)
    def card(
        request: Request,
        post_id: str,
        reviewer: Reviewer = Depends(current_reviewer),
        sec_purpose: str | None = Header(default=None),
        purpose: str | None = Header(default=None),
    ) -> Response:
        record = store.get(post_id)
        if not visible_to(record, reviewer):
            raise HTTPException(403, "channel C only")
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

    @r.get("/posts/{post_id}/approve", response_class=HTMLResponse)
    def approve_form(
        request: Request, post_id: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        _approver(reviewer)
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

    @r.post("/posts/{post_id}/verdict", dependencies=[Depends(check_csrf)])
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
        dec = _enum(Decision, decision, "decision")
        chip = _enum(ReasonCode, reason_code, "reason")
        try:
            record_verdict(
                record,
                dec,
                reviewer=reviewer.name,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
                reason_code=chip,
                opening_line=opening_line,
                spot_check_supported=_spot(spot_check_supported),
            )
        except CardError as e:
            ctx = views.card_context(record, store, reviewer)
            page = "approve.html" if dec == Decision.APPROVE else "card.html"
            return render(request, page, reviewer, 422, error=str(e), **ctx)
        if note.strip():
            record.approval.edits.append(f"note: {note.strip()}")
        store.put(record)
        return RedirectResponse(f"/posts/{post_id}", status_code=303)

    @r.post("/posts/{post_id}/finalise", dependencies=[Depends(check_csrf)])
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
        try:
            finalise_and_issue(
                store,
                record,
                reviewer.name,
                revised_decision=_enum(Decision, revised_decision, "decision"),
                reason_code=_enum(ReasonCode, reason_code, "reason"),
                opening_line=opening_line,
                spot_check_supported=_spot(spot_check_supported),
            )
        except CardError as e:
            ctx = views.card_context(record, store, reviewer)
            return render(request, "card.html", reviewer, 422, error=str(e), **ctx)
        if store.paused:
            return RedirectResponse("/paused", status_code=303)
        return RedirectResponse("/queue", status_code=303)

    # ---- channel C, stage one (D19)
    @r.post("/posts/{post_id}/stage-one", dependencies=[Depends(check_csrf)])
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
                decision=_enum(Decision, decision, "decision"),
                notes=notes,
                time_on_card_s=store.seconds_on_card(post_id, reviewer.name),
            )
        except CardError as e:
            ctx = views.card_context(record, store, reviewer, "ar")
            return render(request, "stage_one.html", reviewer, 422, error=str(e), **ctx)
        store.put(record)
        return RedirectResponse("/queue", status_code=303)

    # ---- paused queue (P14): the record is the approver's; others see only the state
    @r.get("/paused", response_class=HTMLResponse)
    def paused(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        if not reviewer.is_approver:
            return render(
                request, "paused.html", reviewer, rtl=True, last=None, caught=None, missed=None
            )
        missed = [p for p in store.posts.values() if p.flags.canary_outcome == "missed"]
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

    @r.post("/paused/reopen", dependencies=[Depends(check_csrf)])
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
