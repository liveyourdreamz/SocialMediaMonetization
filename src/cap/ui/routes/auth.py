"""Sign-in and sign-out: Google when configured, the per-person code on a laptop."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cap.approval import google
from cap.approval.identity import (
    COOKIE,
    CSRF_COOKIE,
    check_csrf,
    csrf_token,
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
from cap.store import PostStore
from cap.ui.page import app_store, render

router = APIRouter(include_in_schema=False)


def _signed_in(request: Request, name: str) -> Response:
    """A fresh session (and CSRF token) for `name`; any session the browser
    already had is ended, so a planted id never becomes a signed-in one."""
    sessions(request).end(request.cookies.get(COOKIE))
    app_store(request).audit.append("signed in", detail={"who": name})
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
        resp.set_cookie(CSRF_COOKIE, token, httponly=True, samesite="lax", secure=secure_cookies())
    return resp


# ---- sign-in: Google when configured; the per-person secret under the dev flag
@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request) -> Response:
    if not sign_in_configured():
        raise HTTPException(503, "sign-in not configured for this environment")
    return _login_page(request)


@router.get("/auth/google")
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


@router.get("/auth/google/callback", name="google_callback")
def google_callback(request: Request, code: str = "", state: str = "", error: str = "") -> Response:
    if error or not code:
        return _login_page(request, 401, "Google sign-in was cancelled.")
    if not state or state != request.cookies.get(google.STATE_COOKIE):
        raise HTTPException(401, "sign-in state did not match; start again")
    redirect_uri = str(request.url_for("google_callback"))
    email = google.verify_id_token(google.exchange_code(code, redirect_uri))
    return _signed_in(request, google.reviewer_for_email(email))


@router.post("/login", dependencies=[Depends(check_csrf)])
def login(
    request: Request, secret: str = Form(default=""), store: PostStore = Depends(app_store)
) -> Response:
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
        store.audit.append("sign-in refused", detail={"from": client})
        why = throttle.refusal(client) or (
            "That code is not right. Each person's code is shown in the app's "
            "console window on the laptop when it starts."
        )
        return _login_page(request, 403, why)
    if role_for(name) is None:
        return _login_page(request, 403, f"{name} is not on a reviewer list.")
    throttle.succeeded(client)
    return _signed_in(request, name)


@router.post("/logout", dependencies=[Depends(check_csrf)])
def logout(request: Request, store: PostStore = Depends(app_store)) -> Response:
    store.audit.append("signed out")
    sessions(request).end(request.cookies.get(COOKIE))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(COOKIE)
    resp.delete_cookie(CSRF_COOKIE)
    return resp
