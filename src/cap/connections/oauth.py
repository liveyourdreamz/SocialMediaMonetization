"""Connecting an account from the browser: the consent link, the code exchange, the
account lookup, and errors cleaned of anything secret before they are shown."""

from __future__ import annotations

import secrets
from typing import Any
from urllib.parse import urlencode

import httpx

from cap.connections.platforms import PLATFORMS
from cap.connections.store import credentials, is_secret_field, load, save


def new_state() -> str:
    return secrets.token_urlsafe(24)


def auth_url(platform: str, redirect_uri: str, state: str) -> str:
    spec = PLATFORMS[platform]
    creds = credentials(platform)
    if platform == "youtube":
        q = {
            "client_id": creds["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": spec.scopes,
            "state": state,
            "access_type": "offline",
            "prompt": "consent",
            "include_granted_scopes": "true",
        }
    elif platform == "meta":
        q = {
            "client_id": creds["app_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": spec.scopes,
            "state": state,
        }
    else:
        q = {
            "client_key": creds["client_key"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": spec.scopes,
            "state": state,
        }
    return f"{spec.auth_url}?{urlencode(q)}"


def exchange(platform: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """Code -> token JSON (and, for Meta, the page token the uploads use)."""
    spec = PLATFORMS[platform]
    creds = credentials(platform)
    with httpx.Client(timeout=20) as c:
        if platform == "youtube":
            r = c.post(
                spec.token_url,
                data={
                    "code": code,
                    "client_id": creds["client_id"],
                    "client_secret": creds["client_secret"],
                    "redirect_uri": redirect_uri,
                    "grant_type": "authorization_code",
                },
            )
            r.raise_for_status()
            tok = dict(r.json())
            if not tok.get("refresh_token"):
                raise RuntimeError(
                    "Google returned no refresh token; remove the app's access at "
                    "myaccount.google.com/permissions and connect again"
                )
            return tok
        if platform == "meta":
            r = c.get(
                spec.token_url,
                params={
                    "client_id": creds["app_id"],
                    "client_secret": creds["app_secret"],
                    "redirect_uri": redirect_uri,
                    "code": code,
                },
            )
            r.raise_for_status()
            user_tok = r.json()["access_token"]
            # long-lived user token, then the page token (never expires while valid)
            r = c.get(
                "https://graph.facebook.com/v21.0/oauth/access_token",
                params={
                    "grant_type": "fb_exchange_token",
                    "client_id": creds["app_id"],
                    "client_secret": creds["app_secret"],
                    "fb_exchange_token": user_tok,
                },
            )
            r.raise_for_status()
            long_tok = r.json()["access_token"]
            return {"access_token": long_tok, "token_type": "bearer"}
        r = c.post(
            spec.token_url,
            data={
                "client_key": creds["client_key"],
                "client_secret": creds["client_secret"],
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        r.raise_for_status()
        tok = dict(r.json())
        if "access_token" not in tok:
            raise RuntimeError(f"TikTok: {tok.get('error_description') or tok.get('error')}")
        return tok


def fetch_account(platform: str, token: dict[str, Any]) -> dict[str, Any]:
    """The account the token belongs to, for the screen (name, id; Meta: page and IG)."""
    with httpx.Client(timeout=20) as c:
        if platform == "youtube":
            r = c.get(
                "https://www.googleapis.com/youtube/v3/channels",
                params={"part": "snippet", "mine": "true"},
                headers={"Authorization": f"Bearer {token['access_token']}"},
            )
            r.raise_for_status()
            items = r.json().get("items") or []
            if not items:
                raise RuntimeError("this Google account has no YouTube channel")
            ch = items[0]
            return {"name": ch["snippet"]["title"], "id": ch["id"]}
        if platform == "meta":
            r = c.get(
                "https://graph.facebook.com/v21.0/me/accounts",
                params={
                    "fields": "id,name,access_token,instagram_business_account{id,username}",
                    "access_token": token["access_token"],
                },
            )
            r.raise_for_status()
            pages = r.json().get("data") or []
            if not pages:
                raise RuntimeError("this Facebook account manages no Page")
            page = pages[0]
            ig = page.get("instagram_business_account") or {}
            return {
                "name": page["name"],
                "id": page["id"],
                "page_token": page["access_token"],
                "instagram_id": ig.get("id", ""),
                "instagram": ig.get("username", ""),
            }
        r = c.get(
            "https://open.tiktokapis.com/v2/user/info/",
            params={"fields": "open_id,display_name"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        r.raise_for_status()
        u = (r.json().get("data") or {}).get("user") or {}
        return {"name": u.get("display_name", "TikTok account"), "id": u.get("open_id", "")}


def safe_error(e: Exception, platform: str, extra: list[str] | None = None) -> str:
    """An error message fit for a page, a URL or a log: the platform's own reason for
    an HTTP error (never the request URL, which can carry the secret), and every saved
    credential or token value replaced by [redacted]."""
    if isinstance(e, httpx.HTTPStatusError):
        reason = ""
        try:
            body = e.response.json()
            err = body.get("error")
            reason = (
                err.get("message", "")
                if isinstance(err, dict)
                else str(body.get("error_description") or err or "")
            )
        except Exception:  # noqa: BLE001 - a non-JSON body: the status alone
            reason = ""
        text = f"HTTP {e.response.status_code}" + (f": {reason}" if reason else "")
    else:
        text = f"{type(e).__name__}: {e}"
    secrets_ = [v for v in credentials(platform).values() if v]
    stored = load(platform)
    tok = stored.get("token") or {}
    secrets_ += [str(v) for v in tok.values() if isinstance(v, str) and v]
    account = stored.get("account") or {}
    secrets_ += [str(v) for k, v in account.items() if is_secret_field(k) and v]
    secrets_ += [s for s in (extra or []) if s]
    for s in sorted(set(secrets_), key=len, reverse=True):
        if len(s) >= 6:
            text = text.replace(s, "[redacted]")
    return text[:200]


def complete(platform: str, code: str, redirect_uri: str) -> dict[str, Any]:
    """Callback: exchange, look the account up, store both. Returns the account."""
    from datetime import UTC, datetime

    data = load(platform)
    try:
        token = exchange(platform, code, redirect_uri)
        account = fetch_account(platform, token)
    except Exception as e:  # noqa: BLE001 - shown on the screen, never raised to a 500
        data["last_error"] = safe_error(e, platform, [code])
        save(platform, data)
        raise ConnectionError(data["last_error"]) from None
    data["token"] = token
    data["account"] = account
    data["connected_at"] = datetime.now(UTC).isoformat()
    data.pop("last_error", None)
    save(platform, data)
    return account
