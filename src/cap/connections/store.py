"""Where connections are kept: <data_dir>/connections/<platform>.json, owner-only,
never printed, logged or shown back in full; and each platform's status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cap.config import get_settings
from cap.connections.platforms import PLATFORMS
from cap.secretfile import restrict_to_owner


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


def is_secret_field(key: str) -> bool:
    return "token" in key or "secret" in key


def public_account(account: dict[str, Any]) -> dict[str, Any]:
    """The account as a screen may show it: the name and ids, never a token (the Meta
    page token sits beside the page name in the stored record)."""
    return {k: v for k, v in account.items() if not is_secret_field(k)}


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
