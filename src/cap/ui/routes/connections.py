"""The platforms a post can go to: credentials, OAuth connect and callback, disconnect."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from cap.approval.identity import (
    Reviewer,
    check_csrf,
    current_reviewer,
    secure_cookies,
)
from cap.config import get_settings
from cap.store import PostStore
from cap.ui.page import app_store, approver_only, render

router = APIRouter(include_in_schema=False)


# ---- connections: the channels a post can go to -----------------------------
conn_state_cookie = "cap_conn_state"


def _conn_redirect(request: Request, platform: str) -> str:
    base = get_settings().public_base_url.rstrip("/")
    if base:  # behind a proxy or on a public host: the address the platform can reach
        return f"{base}/connections/{platform}/callback"
    return str(request.url_for("connection_callback", platform=platform))


@router.get("/connections", response_class=HTMLResponse)
def connections_page(request: Request, reviewer: Reviewer = Depends(current_reviewer)) -> Response:
    approver_only(reviewer)
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


@router.post("/connections/{platform}/credentials", dependencies=[Depends(check_csrf)])
async def connection_credentials(
    request: Request,
    platform: str,
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    """The developer-app credentials, typed into the form, kept under cap-data only."""
    approver_only(reviewer)
    from cap import connections as conn

    if platform not in conn.PLATFORMS:
        raise HTTPException(404, "no such platform")
    form = await request.form()
    values = {k: str(v) for k, v in form.items() if k != "csrf" and isinstance(v, str)}
    conn.set_credentials(platform, values)
    # which fields were set, never their values
    store.audit.append(
        "platform credentials saved", detail={"platform": platform, "fields": sorted(values)}
    )
    return RedirectResponse(f"/connections?saved={platform}", status_code=303)


@router.get("/connections/{platform}/connect")
def connection_start(
    request: Request, platform: str, reviewer: Reviewer = Depends(current_reviewer)
) -> Response:
    approver_only(reviewer)
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


@router.get("/connections/{platform}/callback", name="connection_callback")
def connection_callback(
    request: Request,
    platform: str,
    code: str = "",
    state: str = "",
    error: str = "",
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
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
    store.audit.append("platform connected", detail={"platform": platform})
    resp = RedirectResponse(f"/connections?connected={platform}", status_code=303)
    resp.delete_cookie(conn_state_cookie)
    return resp


@router.post("/connections/{platform}/disconnect", dependencies=[Depends(check_csrf)])
def connection_disconnect(
    platform: str,
    forget: str = Form(default=""),
    reviewer: Reviewer = Depends(current_reviewer),
    store: PostStore = Depends(app_store),
) -> Response:
    approver_only(reviewer)
    from cap import connections as conn

    if platform not in conn.PLATFORMS:
        raise HTTPException(404, "no such platform")
    (conn.forget if forget else conn.disconnect)(platform)
    store.audit.append(
        "platform forgotten" if forget else "platform disconnected",
        detail={"platform": platform},
    )
    return RedirectResponse("/connections", status_code=303)
