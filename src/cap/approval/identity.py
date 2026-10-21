"""Who is looking at the queue, and what they may do.

Identity is a signed `cap_reviewer` cookie set by `/login` (opt-in, refused outside
local by settings `dev_reviewer_header`). The JSON API also accepts an `X-Reviewer`
header under the same flag, for curl and tests; the HTML app never does. Google
sign-in, when the Cloud project exists, replaces `/login` and sets the same cookie.

Every HTML form carries a CSRF token: a random value in a second cookie, repeated in
the form, compared on every POST (double submit). The cookie is HttpOnly and, outside
local, Secure.

Roles (D5, D19): an approver (settings.approvers) sees every tab and issues tokens; an
Arabic reviewer (settings.arabic_reviewers) sees channel C's stage-one queue and
nothing else. Anyone else is refused.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Cookie, Form, Header, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from cap.config import get_settings

COOKIE = "cap_reviewer"
CSRF_COOKIE = "cap_csrf"
COOKIE_MAX_AGE_S = 12 * 3600

Role = Literal["approver", "arabic"]


@dataclass(frozen=True)
class Reviewer:
    name: str
    role: Role

    @property
    def is_approver(self) -> bool:
        return self.role == "approver"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        get_settings().approval_signing_key.get_secret_value(), salt="cap-reviewer-cookie"
    )


def sign_cookie(name: str) -> str:
    return _serializer().dumps(name)


def read_cookie(value: str) -> str | None:
    try:
        name = _serializer().loads(value, max_age=COOKIE_MAX_AGE_S)
    except BadSignature:
        return None
    return str(name)


def role_for(name: str) -> Role | None:
    s = get_settings()
    if name in s.approvers:
        return "approver"
    if name in s.arabic_reviewers:
        return "arabic"
    return None


def _with_role(name: str) -> Reviewer:
    role = role_for(name)
    if role is None:
        raise HTTPException(403, f"{name} is not on the approver or Arabic-reviewer lists")
    return Reviewer(name, role)


def is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost", "testclient")


def sign_in_configured() -> bool:
    s = get_settings()
    return bool(s.dev_reviewer_header or s.google_enabled)


def reviewer_identity(
    request: Request,
    x_reviewer: str | None = Header(default=None),
    cap_reviewer: str | None = Cookie(default=None),
) -> Reviewer:
    """The JSON API's dependency: the signed cookie, or (dev only, from this machine
    only) an X-Reviewer header; always with a role."""
    s = get_settings()
    if not sign_in_configured():
        raise HTTPException(503, "reviewer sign-in not configured for this environment")
    header_ok = s.dev_reviewer_header and is_loopback(request)
    name = (x_reviewer if header_ok else None) or (
        read_cookie(cap_reviewer) if cap_reviewer else None
    )
    if not name:
        raise HTTPException(401, "sign in required")
    return _with_role(name)


def current_reviewer(cap_reviewer: str | None = Cookie(default=None)) -> Reviewer:
    """The HTML app's dependency: the signed cookie only, with a role."""
    if not sign_in_configured():
        raise HTTPException(503, "reviewer sign-in not configured for this environment")
    name = read_cookie(cap_reviewer) if cap_reviewer else None
    if not name:
        raise HTTPException(401, "sign in required")
    return _with_role(name)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def csrf_token(request: Request) -> str:
    """The token the templates print; empty until sign-in sets the cookie."""
    return request.cookies.get(CSRF_COOKIE, "")


def check_csrf(
    request: Request,
    csrf: str = Form(default=""),
    cap_csrf: str | None = Cookie(default=None),
) -> None:
    """Every HTML POST: the form's token must equal the cookie's (double submit)."""
    if not cap_csrf or not csrf or not secrets.compare_digest(csrf, cap_csrf):
        raise HTTPException(403, "form expired or forged; reload the page and try again")


def secure_cookies() -> bool:
    return get_settings().env not in ("local", "test")
