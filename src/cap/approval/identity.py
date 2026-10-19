"""Who is looking at the queue, and what they may do.

Identity arrives one of two ways, both opt-in and both refused outside local by
settings (`dev_reviewer_header`): an `X-Reviewer` header (the JSON API, tests) or a
signed `cap_reviewer` cookie set by `/login` (the HTML app). Google sign-in, when the
Cloud project exists, replaces `/login` and sets the same cookie; nothing else changes.

Roles (D5, D19): an approver (settings.approvers) sees every tab and issues tokens; an
Arabic reviewer (settings.arabic_reviewers) sees channel C's stage-one queue and
nothing else. Anyone else is refused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import Cookie, Header, HTTPException
from itsdangerous import BadSignature, URLSafeTimedSerializer

from cap.config import get_settings

COOKIE = "cap_reviewer"
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


def reviewer_identity(
    x_reviewer: str | None = Header(default=None),
    cap_reviewer: str | None = Cookie(default=None),
) -> str:
    """The JSON API's dependency: a name, from the header or the cookie."""
    if not get_settings().dev_reviewer_header:
        raise HTTPException(503, "reviewer sign-in not configured for this environment")
    name = x_reviewer or (read_cookie(cap_reviewer) if cap_reviewer else None)
    if not name:
        raise HTTPException(401, "sign in required")
    return name


def current_reviewer(
    x_reviewer: str | None = Header(default=None),
    cap_reviewer: str | None = Cookie(default=None),
) -> Reviewer:
    """The HTML app's dependency: a name with a role, or a refusal."""
    name = reviewer_identity(x_reviewer, cap_reviewer)
    role = role_for(name)
    if role is None:
        raise HTTPException(403, f"{name} is not on the approver or Arabic-reviewer lists")
    return Reviewer(name, role)
