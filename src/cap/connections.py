"""Connections: the platforms a post can go to, the credentials for each, and the
OAuth plumbing that connects Hemant's own accounts from the browser.

Three platforms, each with two parts the app captures on the Connections screen:
  1. the developer-app credentials (a client id and secret Hemant creates in that
     platform's developer console; the screen says where and which redirect URI to
     register), and
  2. the account connection: a Connect button sends him to the platform's consent
     page; the callback stores the token and the account name.

Everything is kept under <data_dir>/connections/<platform>.json, readable by the
owner only (cap.secretfile: 0600, or the user's own ACL on Windows), and is
never printed, logged or shown back in full (the screen shows the last four
characters of a saved id). Nothing here is sent anywhere but the platform it belongs
to. The publisher (plain code, PRD 4.6) reads the tokens; no model ever does.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from cap.config import get_settings
from cap.secretfile import restrict_to_owner


@dataclass(frozen=True)
class PlatformSpec:
    key: str
    label: str
    destinations: tuple[str, ...]  # the cuts it can carry
    fields: tuple[tuple[str, str], ...]  # (field, label) credentials to capture
    console: str  # where the credentials come from
    console_note: str
    auth_url: str
    token_url: str
    scopes: str
    verified: bool  # has the adapter been run against a live account by us


PLATFORMS: dict[str, PlatformSpec] = {
    "youtube": PlatformSpec(
        key="youtube",
        label="YouTube",
        destinations=("youtube_long", "youtube_short"),
        fields=(("client_id", "OAuth client id"), ("client_secret", "OAuth client secret")),
        console="https://console.cloud.google.com/apis/credentials",
        console_note=(
            "Google Cloud console > APIs & Services > Credentials > Create OAuth client "
            "(Web application). Enable the YouTube Data API v3 on the project. The same "
            "client also serves Google sign-in. Then Google Auth Platform > Audience: while "
            "the app's publishing status is Testing, add your own Google account under Test "
            "users or Google refuses the sign-in (403 access_denied); Testing also expires "
            "the token every 7 days, so once Connect works, press Publish app there. "
            "Unverified is fine for your own account: click through the warning once."
        ),
        auth_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        scopes="https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly",
        verified=False,
    ),
    "meta": PlatformSpec(
        key="meta",
        label="Facebook Page and Instagram",
        destinations=("facebook", "instagram"),
        fields=(("app_id", "Meta app id"), ("app_secret", "Meta app secret")),
        console="https://developers.facebook.com/apps/",
        console_note=(
            "Meta for Developers > your app (type Business) > Facebook Login for Business. "
            "The Instagram account must be a professional account linked to the Facebook "
            "Page. Instagram publishing needs the video at a public URL, so on the laptop "
            "only the Facebook Reel is sent."
        ),
        auth_url="https://www.facebook.com/v21.0/dialog/oauth",
        token_url="https://graph.facebook.com/v21.0/oauth/access_token",
        scopes="pages_show_list,pages_manage_posts,pages_read_engagement,instagram_basic,instagram_content_publish,business_management",
        verified=False,
    ),
    "tiktok": PlatformSpec(
        key="tiktok",
        label="TikTok",
        destinations=("tiktok",),
        fields=(("client_key", "Client key"), ("client_secret", "Client secret")),
        console="https://developers.tiktok.com/apps/",
        console_note=(
            "TikTok for Developers > Manage apps > add the Content Posting API product "
            "and request the video.publish scope; unaudited apps can post to the "
            "connected account only as private."
        ),
        auth_url="https://www.tiktok.com/v2/auth/authorize/",
        token_url="https://open.tiktokapis.com/v2/oauth/token/",
        scopes="user.info.basic,video.publish,video.upload",
        verified=False,
    ),
}

DESTINATION_LABELS = {
    "local": "Local folder (always; the record of what went out)",
    "youtube_long": "YouTube: the episode",
    "youtube_short": "YouTube: the Short",
    "facebook": "Facebook Page: Reel",
    "instagram": "Instagram: Reel",
    "tiktok": "TikTok",
}


# ------------------------------------------------------------------ storage


def _dir() -> Path:
    d = Path(get_settings().data_dir) / "connections"
    if not d.exists():
        d.mkdir(parents=True)
        restrict_to_owner(d)
    return d


def _path(platform: str) -> Path:
    return _dir() / f"{platform}.json"


def load(platform: str) -> dict[str, Any]:
    p = _path(platform)
    if not p.exists():
        return {}
    try:
        return dict(json.loads(p.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return {}


def save(platform: str, data: dict[str, Any]) -> None:
    p = _path(platform)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
    restrict_to_owner(tmp)
    tmp.replace(p)


def set_credentials(platform: str, values: dict[str, str]) -> None:
    """Keep the entered fields; a blank field keeps the one already saved."""
    spec = PLATFORMS[platform]
    data = load(platform)
    creds = dict(data.get("credentials", {}))
    for field, _ in spec.fields:
        v = (values.get(field) or "").strip()
        if v:
            creds[field] = v
    data["credentials"] = creds
    save(platform, data)


def credentials(platform: str) -> dict[str, str]:
    """Saved credentials, with .env's Google client as the YouTube fallback."""
    creds = dict(load(platform).get("credentials", {}))
    if platform == "youtube":
        s = get_settings()
        if not creds.get("client_id") and s.google_client_id:
            creds["client_id"] = s.google_client_id
        if not creds.get("client_secret") and s.google_client_secret is not None:
            creds["client_secret"] = s.google_client_secret.get_secret_value()
    return creds


def disconnect(platform: str) -> None:
    data = load(platform)
    for k in ("token", "account", "connected_at", "last_error"):
        data.pop(k, None)
    save(platform, data)


def forget(platform: str) -> None:
    p = _path(platform)
    if p.exists():
        p.unlink()


def _mask(v: str) -> str:
    return ("…" + v[-4:]) if len(v) > 8 else "saved"


def _is_secret_field(key: str) -> bool:
    return "token" in key or "secret" in key


def public_account(account: dict[str, Any]) -> dict[str, Any]:
    """The account as a screen may show it: the name and ids, never a token (the Meta
    page token sits beside the page name in the stored record)."""
    return {k: v for k, v in account.items() if not _is_secret_field(k)}


def status(platform: str) -> dict[str, Any]:
    """What the screen shows: never a secret, never a token."""
    spec = PLATFORMS[platform]
    data = load(platform)
    creds = credentials(platform)
    have = {f: _mask(creds[f]) for f, _ in spec.fields if creds.get(f)}
    return {
        "key": platform,
        "label": spec.label,
        "destinations": spec.destinations,
        "fields": spec.fields,
        "console": spec.console,
        "console_note": spec.console_note,
        "verified": spec.verified,
        "credentials": have,
        "credentials_complete": all(creds.get(f) for f, _ in spec.fields),
        "from_env": platform == "youtube"
        and not load(platform).get("credentials")
        and bool(creds.get("client_id")),
        "connected": bool(data.get("token")),
        "account": public_account(data.get("account") or {}),
        "connected_at": data.get("connected_at", ""),
        "last_error": data.get("last_error", ""),
    }


def connected_destinations() -> list[str]:
    out = ["local"]
    for key, spec in PLATFORMS.items():
        if load(key).get("token"):
            out.extend(spec.destinations)
    return out


# ------------------------------------------------------------------ OAuth


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
    secrets_ += [str(v) for k, v in account.items() if _is_secret_field(k) and v]
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


def youtube_credentials_json() -> dict[str, Any] | None:
    """The google-auth credential dict for the adapter, from the connection store."""
    data = load("youtube")
    tok = data.get("token")
    creds = credentials("youtube")
    if not tok or not creds.get("client_id"):
        return None
    return {
        "token": tok.get("access_token"),
        "refresh_token": tok.get("refresh_token"),
        "token_uri": PLATFORMS["youtube"].token_url,
        "client_id": creds["client_id"],
        "client_secret": creds["client_secret"],
        "scopes": PLATFORMS["youtube"].scopes.split(),
    }


def save_youtube_token(info: dict[str, Any]) -> None:
    """Called by the adapter after a refresh, so the stored token stays current."""
    data = load("youtube")
    tok = dict(data.get("token", {}))
    tok["access_token"] = info.get("token", tok.get("access_token"))
    if info.get("refresh_token"):
        tok["refresh_token"] = info["refresh_token"]
    data["token"] = tok
    save("youtube", data)
