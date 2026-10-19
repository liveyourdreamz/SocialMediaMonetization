"""Publisher: plain code, no model anywhere near it (PRD 4.6, D4, D11, D23).

It receives a post record, its rendered file and an approval token. It verifies the
token against the file's SHA-256, refuses canaries and anything not approved, and
only then calls a platform adapter. Platform adapters are stubs in Foundation; the
YouTube adapter arrives with the week-3 milestone.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cap.approval.token import ApprovalToken, sha256_file, verify
from cap.schema.post_record import PostRecord, PostStatus


class PublishRefusedError(RuntimeError):
    pass


class PlatformAdapter(Protocol):
    name: str

    def upload(self, record: PostRecord, asset: Path) -> str:
        """Upload and return the platform post id."""


class DryRunAdapter:
    """Writes nothing anywhere. Used until the platform accounts exist."""

    name = "dry_run"

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []

    def upload(self, record: PostRecord, asset: Path) -> str:
        pid = f"dry-{record.post_id[:8]}"
        self.uploads.append((record.post_id, pid))
        return pid


def publish(
    record: PostRecord, asset: Path, token: ApprovalToken, adapter: PlatformAdapter
) -> PostRecord:
    if record.flags.canary:
        raise PublishRefusedError("flagged record: canary")
    if record.status != PostStatus.APPROVED:
        raise PublishRefusedError(f"record status is {record.status.value}")
    if not record.gates.all_passed:
        raise PublishRefusedError("a gate did not pass; R-20 forbids any override here")
    if record.sponsor is not None and record.sponsor.permit_status != "granted":
        raise PublishRefusedError("sponsored post without the UAE permit (PRD 4.6)")
    if not record.asset.sha256:
        raise PublishRefusedError("record carries no asset hash")
    digest = sha256_file(asset)
    if record.asset.sha256 != digest:
        raise PublishRefusedError("rendered file changed since the record was approved")
    ok, reason = verify(token, record.post_id, digest)
    if not ok:
        raise PublishRefusedError(f"token rejected: {reason}")
    post_id = adapter.upload(record, asset)
    record.publish.platform_post_ids[adapter.name] = post_id
    record.publish.published_at = datetime.now(UTC)
    record.status = PostStatus.PUBLISHED
    record.log(f"published to {adapter.name} as {post_id}")
    return record
