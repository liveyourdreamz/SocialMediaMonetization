"""Approval token: the only thing the publisher accepts (D4, D11).

The token is an HMAC-signed statement that reviewer R approved post P whose rendered
file hashes to H at time T. It is bound to the SHA-256 of the exact file, so a
re-render after approval invalidates it, and it carries the canary flag so a seeded
post can never be published even with a valid signature (D23).

Signing key: settings.approval_signing_key (Secret Manager in production). The local
default is for tests; `assert_production_key()` refuses to run outside local with it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from cap.config import LOCAL_DEFAULT_KEY, get_settings


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


@dataclass(frozen=True)
class ApprovalToken:
    post_id: str
    asset_sha256: str
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
        return ApprovalToken(**json.loads(s))


def _key() -> bytes:
    return get_settings().approval_signing_key.get_secret_value().encode()


def assert_production_key() -> None:
    s = get_settings()
    if s.env != "local" and s.approval_signing_key.get_secret_value() == LOCAL_DEFAULT_KEY:
        raise RuntimeError("CAP_APPROVAL_SIGNING_KEY must be set outside the local environment")


def issue(
    post_id: str, asset_sha256: str, reviewer: str, decision: str, canary: bool
) -> ApprovalToken:
    assert_production_key()
    t = ApprovalToken(
        post_id=post_id,
        asset_sha256=asset_sha256,
        reviewer=reviewer,
        decision=decision,
        canary=canary,
        issued_at=datetime.now(UTC).isoformat(),
    )
    sig = hmac.new(_key(), t.payload(), hashlib.sha256).hexdigest()
    return ApprovalToken(**{**asdict(t), "signature": sig})


def verify(token: ApprovalToken, post_id: str, asset_sha256: str) -> tuple[bool, str]:
    """Constant-time signature check plus binding checks. Returns (ok, reason)."""
    expected = hmac.new(_key(), token.payload(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, token.signature):
        return False, "bad signature"
    if token.post_id != post_id:
        return False, "token is for another post"
    if token.asset_sha256 != asset_sha256:
        return False, "asset hash does not match the approved file"
    if token.decision != "approve":
        return False, f"decision is {token.decision}, not approve"
    if token.canary:
        return False, "canary posts are never published"
    return True, "ok"
