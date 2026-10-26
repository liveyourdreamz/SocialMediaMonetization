"""Approval token: the only thing the publisher accepts (D4, D11).

The token is an HMAC-signed statement that reviewer R approved post P whose release
manifest hashes to H at time T. The manifest (cap.release) names every file by role
with its SHA-256 and every title, caption, description, tag and platform flag that
leaves the machine, so changing any of them after approval invalidates the token. It
carries the canary flag so a seeded post can never be published even with a valid
signature (D23).

Signing key: settings.approval_signing_key (Secret Manager in production). The local
default is for tests; `assert_production_key()` refuses to run outside local with it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cap.config import LOCAL_DEFAULT_KEY, get_settings
from cap.policy import TOKEN_TTL_HOURS

EXPIRED = "token has expired"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ApprovalToken:
    post_id: str
    manifest_sha256: str
    reviewer: str
    decision: str
    canary: bool
    issued_at: str
    signature: str = ""

    def payload(self) -> bytes:
        d = asdict(self)
        d.pop("signature")
        return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()

    def encode(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))

    @staticmethod
    def decode(s: str) -> ApprovalToken:
        """Raises ValueError for anything that is not a token of this shape (a token
        from before the release manifest bound only a file hash and is refused)."""
        try:
            d = json.loads(s)
        except json.JSONDecodeError as e:
            raise ValueError("not a token") from e
        names = {f.name for f in fields(ApprovalToken)}
        if not isinstance(d, dict) or set(d) != names:
            raise ValueError("not a token of this version")
        return ApprovalToken(**d)


def _key() -> bytes:
    return get_settings().approval_signing_key.get_secret_value().encode()


def _sign(t: ApprovalToken) -> str:
    return hmac.new(_key(), t.payload(), hashlib.sha256).hexdigest()


def assert_production_key() -> None:
    s = get_settings()
    if (
        s.env not in ("local", "test")
        and s.approval_signing_key.get_secret_value() == LOCAL_DEFAULT_KEY
    ):
        raise RuntimeError("CAP_APPROVAL_SIGNING_KEY must be set outside the local environment")


def issue(
    post_id: str,
    manifest_sha256: str,
    reviewer: str,
    decision: str,
    canary: bool,
    *,
    now: datetime | None = None,
) -> ApprovalToken:
    assert_production_key()
    if reviewer not in get_settings().approvers:
        raise PermissionError(f"{reviewer} is not an authorised approver (D5)")
    if not manifest_sha256:
        raise ValueError("no manifest hash to bind the token to")
    t = ApprovalToken(
        post_id=post_id,
        manifest_sha256=manifest_sha256,
        reviewer=reviewer,
        decision=decision,
        canary=canary,
        issued_at=(now or datetime.now(UTC)).isoformat(),
    )
    return ApprovalToken(**{**asdict(t), "signature": _sign(t)})


def verify(
    token: ApprovalToken, post_id: str, manifest_sha256: str, *, now: datetime | None = None
) -> tuple[bool, str]:
    """Constant-time signature check plus binding checks. Returns (ok, reason). A
    token is valid for strictly less than TOKEN_TTL_HOURS; at the limit it expired."""
    if not hmac.compare_digest(_sign(token), token.signature):
        return False, "bad signature"
    if token.post_id != post_id:
        return False, "token is for another post"
    if token.manifest_sha256 != manifest_sha256:
        return False, "manifest hash does not match the approved release"
    if token.decision != "approve":
        return False, f"decision is {token.decision}, not approve"
    if token.canary:
        return False, "canary posts are never published"
    if token.reviewer not in get_settings().approvers:
        return False, "token reviewer is not an authorised approver"
    try:
        issued = datetime.fromisoformat(token.issued_at)
    except ValueError:
        return False, "token has no valid issue time"
    if issued.tzinfo is None:
        return False, "token has no valid issue time"
    if (now or datetime.now(UTC)) - issued >= timedelta(hours=TOKEN_TTL_HOURS):
        return False, EXPIRED
    return True, "ok"
