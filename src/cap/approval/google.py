"""Google sign-in (OpenID Connect, authorization code flow) for the approval app.

On: when settings carry a client id and secret (locally from .env, in production from
Secret Manager). The signed-in e-mail must appear in settings.reviewer_emails, which
maps it to a reviewer name on the approver or Arabic-reviewer lists; anyone else is
refused. The result is the same signed cookie the local stand-in sets, so nothing
downstream knows which door was used.

The id_token is verified against Google's published keys (issuer, audience, expiry,
e-mail verified) with PyJWT; the code exchange is a plain HTTPS POST with httpx.
"""

from __future__ import annotations

import secrets
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException

from cap.config import get_settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")
STATE_COOKIE = "cap_oauth_state"

_jwks: jwt.PyJWKClient | None = None


def new_state() -> str:
    return secrets.token_urlsafe(24)


def auth_url(redirect_uri: str, state: str) -> str:
    s = get_settings()
    q = {
        "client_id": s.google_client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email",
        "state": state,
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urlencode(q)}"


def exchange_code(code: str, redirect_uri: str) -> str:
    """Code -> id_token (the only field the app uses)."""
    s = get_settings()
    assert s.google_client_secret is not None
    r = httpx.post(
        TOKEN_URL,
        data={
            "code": code,
            "client_id": s.google_client_id,
            "client_secret": s.google_client_secret.get_secret_value(),
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if r.status_code != 200:
        raise HTTPException(401, "Google did not accept the sign-in code")
    return str(r.json()["id_token"])


def verify_id_token(id_token: str) -> str:
    """Returns the verified, Google-confirmed e-mail address."""
    global _jwks
    if _jwks is None:
        _jwks = jwt.PyJWKClient(JWKS_URL, cache_keys=True)
    key = _jwks.get_signing_key_from_jwt(id_token)
    claims = jwt.decode(
        id_token,
        key.key,
        algorithms=["RS256"],
        audience=get_settings().google_client_id,
        issuer=list(ISSUERS),
    )
    if not claims.get("email_verified"):
        raise HTTPException(401, "Google has not verified that e-mail address")
    return str(claims["email"]).lower()


def reviewer_for_email(email: str) -> str:
    name = get_settings().reviewer_emails.get(email.lower())
    if not name:
        raise HTTPException(403, f"{email} is not on the reviewer list")
    return name
