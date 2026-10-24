"""Who is looking at the queue, and what they may do.

Identity is a server-held session (cap.approval.sessions) whose random id travels in
the `cap_reviewer` cookie. Two doors open a session: Google sign-in when the Cloud
project exists, and the laptop sign-in, where each person types their own secret
(settings.signin_secrets, generated into .env by cap.setup). The secret alone says who
the person is; no name is typed, so one reviewer cannot sign in as the other. Where
the request comes from (the laptop itself or the phone) never matters.

The X-Reviewer header is honoured only under the test-only setting (test_client with
CAP_ENV=test); `cap serve` never honours it.

Every HTML form, the sign-in form included, carries a CSRF token: a random value in a
second cookie, repeated in the form, compared on every POST (double submit). Both
cookies are HttpOnly and, outside local, Secure.

Roles (D5, D19): an approver (settings.approvers) sees every tab and issues tokens; an
Arabic reviewer (settings.arabic_reviewers) sees channel C's stage-one queue and
nothing else. Anyone else is refused.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Literal

from fastapi import Cookie, Form, Header, HTTPException, Request

from cap.approval.sessions import SessionStore
from cap.config import get_settings

COOKIE = "cap_reviewer"
CSRF_COOKIE = "cap_csrf"

Role = Literal["approver", "arabic"]


@dataclass(frozen=True)
class Reviewer:
    name: str
    role: Role

    @property
    def is_approver(self) -> bool:
        return self.role == "approver"


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


def normalise_secret(typed: str) -> str:
    """What a phone keyboard adds is ignored: case, spaces and dashes."""
    return "".join(ch for ch in typed.lower() if ch.isalnum())


def person_for_secret(typed: str) -> str | None:
    """The person whose sign-in secret this is, or None. Every secret is compared, in
    constant time, whatever matched first."""
    want = normalise_secret(typed).encode()
    found: str | None = None
    for person, secret in get_settings().signin_secrets.items():
        have = normalise_secret(secret.get_secret_value()).encode()
        if want and len(have) >= 10 and secrets.compare_digest(want, have):
            found = person
    return found


def sessions(request: Request) -> SessionStore:
    store: SessionStore = request.app.state.sessions
    return store


def signed_in_name(request: Request) -> str | None:
    return sessions(request).name_for(request.cookies.get(COOKIE))


def sign_in_configured() -> bool:
    s = get_settings()
    return bool(s.dev_reviewer_header or s.google_enabled)


def reviewer_identity(request: Request, x_reviewer: str | None = Header(default=None)) -> Reviewer:
    """The JSON API's dependency: the session, or (test suite only) an X-Reviewer
    header; always with a role."""
    if not sign_in_configured():
        raise HTTPException(503, "reviewer sign-in not configured for this environment")
    name = (x_reviewer if get_settings().test_client_on else None) or signed_in_name(request)
    if not name:
        raise HTTPException(401, "sign in required")
    return _with_role(name)


def current_reviewer(request: Request) -> Reviewer:
    """The HTML app's dependency: the session only, with a role."""
    if not sign_in_configured():
        raise HTTPException(503, "reviewer sign-in not configured for this environment")
    name = signed_in_name(request)
    if not name:
        raise HTTPException(401, "sign in required")
    return _with_role(name)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def csrf_token(request: Request) -> str:
    """The token the templates print; empty until a cookie carries one."""
    return request.cookies.get(CSRF_COOKIE, "")


def check_csrf(
    csrf: str = Form(default=""),
    cap_csrf: str | None = Cookie(default=None),
) -> None:
    """Every HTML POST: the form's token must equal the cookie's (double submit)."""
    if not cap_csrf or not csrf or not secrets.compare_digest(csrf, cap_csrf):
        raise HTTPException(403, "form expired or forged; reload the page and try again")


def secure_cookies() -> bool:
    return get_settings().env not in ("local", "test")
