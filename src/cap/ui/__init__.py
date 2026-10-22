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

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from cap import release
from cap.approval import google, release_job
from cap.approval.app import Store, finalise_and_issue, publish_now, visible_to
from cap.approval.card import CardError, record_stage_one, record_verdict
from cap.approval.identity import (
    COOKIE,
    CSRF_COOKIE,
    Reviewer,
    check_csrf,
    csrf_token,
    current_reviewer,
    new_csrf_token,
    person_for_secret,
    role_for,
    secure_cookies,
    sessions,
    sign_in_configured,
)
from cap.approval.sessions import LIFETIME_S
from cap.approval.throttle import SignInThrottle
from cap.config import get_settings
from cap.publisher import PublishRefusedError
from cap.schema.post_record import Decision, PostStatus, ReasonCode
from cap.ui import tour, views

HERE = Path(__file__).resolve().parent
STATIC = StaticFiles(directory=str(HERE / "static"))
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.filters["minutes"] = views.minutes
templates.env.filters["day"] = views.day
templates.env.globals["workflow"] = views.workflow


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

    def _signed_in(request: Request, name: str) -> Response:
        """A fresh session (and CSRF token) for `name`; any session the browser
        already had is ended, so a planted id never becomes a signed-in one."""
        sessions(request).end(request.cookies.get(COOKIE))
        resp = RedirectResponse("/", status_code=303)
        secure = secure_cookies()
        sid = sessions(request).start(name)
        for key, value in ((COOKIE, sid), (CSRF_COOKIE, new_csrf_token())):
            resp.set_cookie(
                key, value, max_age=LIFETIME_S, httponly=True, samesite="lax", secure=secure
            )
        resp.delete_cookie(google.STATE_COOKIE)
        return resp

    def _login_page(request: Request, status: int = 200, error: str = "") -> Response:
        """The sign-in form, with a CSRF token of its own (set here when the browser
        has none yet, so the first POST can be checked like every other)."""
        s = get_settings()
        token = csrf_token(request) or new_csrf_token()
        resp = render(
            request,
            "login.html",
            None,
            status,
            csrf=token,
            error=error,
            google=s.google_enabled,
            local=s.dev_reviewer_header,
        )
        if not csrf_token(request):
            resp.set_cookie(
                CSRF_COOKIE, token, httponly=True, samesite="lax", secure=secure_cookies()
            )
        return resp

    # ---- sign-in: Google when configured; the per-person secret under the dev flag
    @r.get("/login", response_class=HTMLResponse)
    def login_form(request: Request) -> Response:
        if not sign_in_configured():
            raise HTTPException(503, "sign-in not configured for this environment")
        return _login_page(request)

    @r.get("/auth/google")
    def google_start(request: Request) -> Response:
        if not get_settings().google_enabled:
            raise HTTPException(503, "Google sign-in is not configured")
        state = google.new_state()
        redirect_uri = str(request.url_for("google_callback"))
        resp = RedirectResponse(google.auth_url(redirect_uri, state), status_code=303)
        resp.set_cookie(
            google.STATE_COOKIE,
            state,
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=secure_cookies(),
        )
        return resp

    @r.get("/auth/google/callback", name="google_callback")
    def google_callback(
        request: Request, code: str = "", state: str = "", error: str = ""
    ) -> Response:
        if error or not code:
            return _login_page(request, 401, "Google sign-in was cancelled.")
        if not state or state != request.cookies.get(google.STATE_COOKIE):
            raise HTTPException(401, "sign-in state did not match; start again")
        redirect_uri = str(request.url_for("google_callback"))
        email = google.verify_id_token(google.exchange_code(code, redirect_uri))
        return _signed_in(request, google.reviewer_for_email(email))

    @r.post("/login", dependencies=[Depends(check_csrf)])
    def login(request: Request, secret: str = Form(default="")) -> Response:
        """The secret alone says who is signing in. Wrong secrets count against this
        device and against everyone (cap.approval.throttle)."""
        if not get_settings().dev_reviewer_header:
            raise HTTPException(503, "sign-in not configured for this environment")
        throttle: SignInThrottle = request.app.state.throttle
        client = request.client.host if request.client else ""
        refused = throttle.refusal(client)
        if refused:
            return _login_page(request, 429, refused)
        name = person_for_secret(secret)
        if name is None:
            throttle.failed(client)
            why = throttle.refusal(client) or (
                "That code is not right. Each person's code is shown in the app's "
                "console window on the laptop when it starts."
            )
            return _login_page(request, 403, why)
        if role_for(name) is None:
            return _login_page(request, 403, f"{name} is not on a reviewer list.")
        throttle.succeeded(client)
        return _signed_in(request, name)

    @r.post("/logout", dependencies=[Depends(check_csrf)])
    def logout(request: Request) -> Response:
        sessions(request).end(request.cookies.get(COOKIE))
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
        acknowledged: str | None = Form(default=None),
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
                acknowledged=acknowledged == "yes",
            )
        except CardError as e:
            ctx = views.card_context(record, store, reviewer)
            return render(request, "card.html", reviewer, 422, error=str(e), **ctx)
        if store.paused:
            return RedirectResponse("/paused", status_code=303)
        return RedirectResponse("/queue", status_code=303)

    # ---- the outbox: what goes out, and the publish step (PRD 4.6)
    @r.get("/posts/{post_id}/preview", response_class=HTMLResponse)
    def preview(
        request: Request, post_id: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        _approver(reviewer)
        record = store.get(post_id)
        from cap import connections as conn

        available = conn.connected_destinations()
        return render(
            request,
            "preview.html",
            reviewer,
            record=record,
            pkg=views.package(record),
            destinations=[
                (k, label, k in available) for k, label in conn.DESTINATION_LABELS.items()
            ],
            any_connected=len(available) > 1,
            results=views.publish_results(record),
            release=views.release_view(record),
            flags=release.flags_for(record),
            local_dir=str((store.local_published_dir() / record.post_id).resolve()),
            content_rtl=views.is_rtl(record.channel_id.value),
            channel_label=views.channel_label(record.channel_id.value),
            refusal=request.query_params.get("refused", ""),
        )

    @r.post("/posts/{post_id}/publish", dependencies=[Depends(check_csrf)])
    async def publish_post(
        request: Request, post_id: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        _approver(reviewer)
        if store.paused:
            return RedirectResponse("/paused", status_code=303)
        record = store.get(post_id)
        form = await request.form()
        from cap.connections import DESTINATION_LABELS

        chosen = [
            str(d)
            for d in form.getlist("destinations")
            if isinstance(d, str) and d in DESTINATION_LABELS and d != "local"
        ]
        # uploads take minutes: off the event loop, so the app keeps answering (H5)
        refused = await run_in_threadpool(publish_now, store, record, chosen)
        return _back_to_outbox(post_id, refused)

    def _back_to_outbox(post_id: str, refused: str | None) -> Response:
        if refused:
            from urllib.parse import quote

            return RedirectResponse(
                f"/posts/{post_id}/preview?refused={quote(refused)}", status_code=303
            )
        return RedirectResponse(f"/posts/{post_id}/preview", status_code=303)

    @r.post("/posts/{post_id}/release/retry", dependencies=[Depends(check_csrf)])
    def release_retry(post_id: str, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        """Make the final video again after a failure (or for the first time for a post
        approved before final renders existed)."""
        _approver(reviewer)
        if store.paused:
            return RedirectResponse("/paused", status_code=303)
        record = store.get(post_id)
        if not release_job.can_start(record):
            return _back_to_outbox(post_id, "the final video is ready or already being made")
        try:
            release_job.start(store, record)
        except PublishRefusedError as e:
            return _back_to_outbox(post_id, str(e))
        return _back_to_outbox(post_id, None)

    @r.post("/posts/{post_id}/release/reissue", dependencies=[Depends(check_csrf)])
    def release_reissue(post_id: str, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        """M4: a fresh token for an unchanged release (tokens expire after a week)."""
        _approver(reviewer)
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

    @r.post("/operator/brief", dependencies=[Depends(check_csrf)])
    def file_brief(
        request: Request,
        channel: str = Form(...),
        topic: str = Form(...),
        urls: str = Form(default=""),
        test_log: str = Form(default=""),
        media: list[UploadFile] = File(default=[]),
        reviewer: Reviewer = Depends(current_reviewer),
    ) -> Response:
        """Brief -> draft -> critic -> adapt -> gates -> render -> queue, in the
        background (a real draft plus a rendered video takes a few minutes). The record
        appears on the Operator screen as 'drafting' at once; a fault parks it with the
        reason on the record, never a blank 500."""
        _approver(reviewer)
        import threading

        from cap.demo.seed import new_brief, run_brief

        topic = topic.strip()[:140]
        test_log = test_log[:20_000]
        if not topic:
            raise HTTPException(422, "a topic is needed")
        auto = channel == "auto"
        how = ""
        if auto:
            from cap.routing import route

            channel, how = route(topic.strip())
        elif channel not in get_settings().channels:
            raise HTTPException(422, "unknown channel")
        record = new_brief(channel, topic.strip())
        if auto:
            record.log(
                f"channel: {channel} · {get_settings().channel_characters.get(channel, '')} ({how})"
            )
        # screenshots from the test: the renderer shows them in the screen panel, in order
        saved = 0
        for i, up in enumerate(media[:10]):  # at most ten screenshots per brief
            name = Path((up.filename or "").replace("\\", "/")).name
            if not name or Path(name).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                continue
            data = up.file.read(15_000_001)  # never read past the limit
            if not data or len(data) > 15_000_000:
                continue
            try:  # the contents must be an image, not just the name
                import io

                from PIL import Image

                Image.open(io.BytesIO(data)).verify()
            except Exception:  # noqa: BLE001 - not an image: skipped
                continue
            folder = Path(get_settings().data_dir) / "media" / record.post_id
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"{i:02d}-{name}").write_bytes(data)
            saved += 1
        if saved:
            record.log(f"{saved} screenshot(s) attached; they take the screen panel in order")
            record.media_manifest.real_place_or_event_depicted = False
        store.put(record)
        url_list = [u.strip() for u in urls.replace(",", "\n").splitlines() if u.strip()]

        def work() -> None:
            try:
                run_brief(
                    store, channel, topic.strip(), urls=url_list, test_log=test_log, record=record
                )
            except Exception as e:  # noqa: BLE001
                record.stop(
                    "draft",
                    f"the pipeline could not run: {type(e).__name__}: {str(e)[:300]}",
                    PostStatus.PARKED,
                )
                record.log(
                    f"parked: the pipeline could not run: {type(e).__name__}: {str(e)[:300]}"
                )
                store.put(record)

        threading.Thread(target=work, name=f"brief-{record.post_id[:8]}", daemon=True).start()
        return RedirectResponse("/operator", status_code=303)

    def _connections_summary() -> str:
        from cap import connections as conn

        names = [
            conn.status(k)["account"].get("name", k)
            for k in conn.PLATFORMS
            if conn.status(k)["connected"]
        ]
        return ("connected: " + ", ".join(names)) if names else "none connected yet"

    # ---- connections: the channels a post can go to -----------------------------
    conn_state_cookie = "cap_conn_state"

    def _conn_redirect(request: Request, platform: str) -> str:
        base = get_settings().public_base_url.rstrip("/")
        if base:  # behind a proxy or on a public host: the address the platform can reach
            return f"{base}/connections/{platform}/callback"
        return str(request.url_for("connection_callback", platform=platform))

    @r.get("/connections", response_class=HTMLResponse)
    def connections_page(
        request: Request, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        _approver(reviewer)
        from cap import connections as conn

        return render(
            request,
            "connections.html",
            reviewer,
            tab="operator",
            platforms=[conn.status(k) for k in conn.PLATFORMS],
            redirect_uris={k: _conn_redirect(request, k) for k in conn.PLATFORMS},
            signin_uri=str(request.url_for("google_callback")),
            saved=request.query_params.get("saved", ""),
            connected=request.query_params.get("connected", ""),
            error=request.query_params.get("error", ""),
        )

    @r.post("/connections/{platform}/credentials", dependencies=[Depends(check_csrf)])
    async def connection_credentials(
        request: Request, platform: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        """The developer-app credentials, typed into the form, kept under cap-data only."""
        _approver(reviewer)
        from cap import connections as conn

        if platform not in conn.PLATFORMS:
            raise HTTPException(404, "no such platform")
        form = await request.form()
        values = {k: str(v) for k, v in form.items() if k != "csrf" and isinstance(v, str)}
        conn.set_credentials(platform, values)
        return RedirectResponse(f"/connections?saved={platform}", status_code=303)

    @r.get("/connections/{platform}/connect")
    def connection_start(
        request: Request, platform: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        _approver(reviewer)
        from cap import connections as conn

        if platform not in conn.PLATFORMS:
            raise HTTPException(404, "no such platform")
        if not conn.status(platform)["credentials_complete"]:
            return RedirectResponse(
                f"/connections?error={platform}: enter the credentials first", status_code=303
            )
        state = conn.new_state()
        resp = RedirectResponse(
            conn.auth_url(platform, _conn_redirect(request, platform), state), status_code=303
        )
        resp.set_cookie(
            conn_state_cookie,
            f"{platform}:{state}",
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=secure_cookies(),
        )
        return resp

    @r.get("/connections/{platform}/callback", name="connection_callback")
    def connection_callback(
        request: Request,
        platform: str,
        code: str = "",
        state: str = "",
        error: str = "",
        reviewer: Reviewer = Depends(current_reviewer),
    ) -> Response:
        _approver(reviewer)
        from urllib.parse import quote

        from cap import connections as conn

        if platform not in conn.PLATFORMS:
            raise HTTPException(404, "no such platform")
        expected = request.cookies.get(conn_state_cookie, "")
        if error or not code:
            return RedirectResponse(
                f"/connections?error={quote(f'{platform}: {error or "no code returned"}')}",
                status_code=303,
            )
        if expected != f"{platform}:{state}":
            return RedirectResponse(
                f"/connections?error={quote(f'{platform}: the sign-in did not start here; try again')}",
                status_code=303,
            )
        try:
            conn.complete(platform, code, _conn_redirect(request, platform))
        except Exception:  # noqa: BLE001 - the card shows the cleaned reason (last_error)
            resp = RedirectResponse(
                f"/connections?error={quote(f'{platform}: connecting failed; the reason is on its card')}",
                status_code=303,
            )
            resp.delete_cookie(conn_state_cookie)
            return resp
        resp = RedirectResponse(f"/connections?connected={platform}", status_code=303)
        resp.delete_cookie(conn_state_cookie)
        return resp

    @r.post("/connections/{platform}/disconnect", dependencies=[Depends(check_csrf)])
    def connection_disconnect(
        platform: str,
        forget: str = Form(default=""),
        reviewer: Reviewer = Depends(current_reviewer),
    ) -> Response:
        _approver(reviewer)
        from cap import connections as conn

        if platform not in conn.PLATFORMS:
            raise HTTPException(404, "no such platform")
        (conn.forget if forget else conn.disconnect)(platform)
        return RedirectResponse("/connections", status_code=303)

    # ---- guided tour: one worked Patch episode, idea to published ----------------
    @r.get("/tour", response_class=HTMLResponse)
    def tour_intro(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        _approver(reviewer)
        current = [p for p in store.posts.values() if p.flags.tour]
        current.sort(key=lambda p: p.created_at, reverse=True)
        return render(
            request,
            "tour_intro.html",
            reviewer,
            stops=tour.STOPS,
            current=current[0] if current else None,
        )

    @r.post("/tour/start", dependencies=[Depends(check_csrf)])
    def tour_start(reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        _approver(reviewer)
        from cap.demo.seed import start_tour

        rec = start_tour(store)
        return RedirectResponse(f"/tour/{rec.post_id}/1", status_code=303)

    @r.get("/tour/{post_id}/{n}", response_class=HTMLResponse)
    def tour_stop(
        request: Request, post_id: str, n: int, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        _approver(reviewer)
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
            stops=tour.STOPS,
            platforms=[conn.status(k) for k in conn.PLATFORMS],
            results=views.publish_results(record),
            local_dir=str((store.local_published_dir() / record.post_id).resolve()),
            **tour.context(record, n, pkg),
        )

    def _send(record_files: list[str], name: str) -> Response:
        from fastapi.responses import FileResponse

        path = {Path(p).name: Path(p) for p in record_files if p}.get(name)
        if path is None or not path.exists():
            raise HTTPException(404, "no such rendered file")
        return FileResponse(path)

    @r.get("/assets/{post_id}/final/{name}")
    def final_asset(
        post_id: str, name: str, reviewer: Reviewer = Depends(current_reviewer)
    ) -> Response:
        """The final release's files (the opening line spoken): what is published."""
        record = store.get(post_id)
        if not visible_to(record, reviewer):
            raise HTTPException(403, "channel C only")
        return _send([f.uri for f in record.release.files], name)

    @r.get("/assets/{post_id}/{name}")
    def asset(post_id: str, name: str, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
        """The review render of one post, to the people who may see the post."""
        record = store.get(post_id)
        if not visible_to(record, reviewer):
            raise HTTPException(403, "channel C only")
        a = record.asset
        return _send([a.uri, a.short_uri, a.thumbnail_uri], name)

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
            published=views.published(store),
            recent=views.recent_briefs(store),
            outbox=views.outbox(store),
            connections_summary=_connections_summary(),
            build=__import__("cap.version", fromlist=["build_id"]).build_id(),
            running=any(
                p.status.value in ("drafting", "gated", "rendering") for p in store.posts.values()
            ),
        )

    return r
