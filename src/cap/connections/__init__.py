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

Modules: platforms, store, oauth; every name is available from cap.connections.
"""

from __future__ import annotations

from cap.connections.oauth import auth_url, complete, exchange, fetch_account, new_state, safe_error
from cap.connections.platforms import DESTINATION_LABELS, PLATFORMS, PlatformSpec
from cap.connections.store import (
    connected_destinations,
    credentials,
    disconnect,
    forget,
    load,
    public_account,
    save,
    save_youtube_token,
    set_credentials,
    status,
    youtube_credentials_json,
)

__all__ = [
    "DESTINATION_LABELS",
    "PLATFORMS",
    "PlatformSpec",
    "auth_url",
    "complete",
    "connected_destinations",
    "credentials",
    "disconnect",
    "exchange",
    "fetch_account",
    "forget",
    "load",
    "new_state",
    "public_account",
    "safe_error",
    "save",
    "save_youtube_token",
    "set_credentials",
    "status",
    "youtube_credentials_json",
]
