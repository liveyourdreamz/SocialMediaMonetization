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


class LocalFolderAdapter:
    """The laptop build's platform: copies the rendered file into <dir>/<post_id>/
    with a manifest of what was approved and by whom. The YouTube and Meta adapters
    replace it with the same one method."""

    name = "local"

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def upload(self, record: PostRecord, asset: Path) -> str:
        import json
        import shutil

        out = self.directory / record.post_id
        out.mkdir(parents=True, exist_ok=True)
        shutil.copy2(asset, out / asset.name)
        for extra in (record.asset.short_uri, record.asset.thumbnail_uri):
            if extra and Path(extra).exists():
                shutil.copy2(extra, out / Path(extra).name)
        manifest = {
            "post_id": record.post_id,
            "channel": record.channel_id.value,
            "title": record.draft.title,
            "approved_by": record.approval.reviewer,
            "opening_line": record.human_contribution.text,
            "asset": asset.name,
            "sha256": record.asset.sha256,
            "published_at": datetime.now(UTC).isoformat(),
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
        return f"local:{out}"


def publish(
    record: PostRecord, asset: Path, token: ApprovalToken, adapter: PlatformAdapter
) -> PostRecord:
    if record.flags.canary:
        raise PublishRefusedError("flagged record: canary")
    if record.flags.tour and not isinstance(adapter, LocalFolderAdapter | DryRunAdapter):
        raise PublishRefusedError("a guided-tour post never leaves this computer")
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


def publish_to(
    record: PostRecord,
    asset: Path,
    token: ApprovalToken,
    adapters: list[PlatformAdapter],
) -> PostRecord:
    """The same checks once, then every chosen destination in turn. One platform
    failing never undoes another: each result or error is recorded against its
    name, and the post is published if at least one destination took it."""
    if not adapters:
        raise PublishRefusedError("no destination chosen")
    first, rest = adapters[0], adapters[1:]
    publish(record, asset, token, first)  # the checks, then the first destination
    for adapter in rest:
        try:
            pid = adapter.upload(record, asset)
            record.publish.platform_post_ids[adapter.name] = pid
            record.log(f"published to {adapter.name} as {pid}")
        except Exception as e:  # noqa: BLE001 - recorded per destination, shown on the Outbox
            record.publish.errors[adapter.name] = f"{type(e).__name__}: {str(e)[:200]}"
            record.log(f"{adapter.name} refused or failed: {type(e).__name__}")
    if rest and not any(a.name in record.publish.platform_post_ids for a in rest):
        # a platform was chosen and none took it: the local copy is a record, not a
        # publication (review H3). Back to approved so a retry from the Outbox works.
        record.publish.platform_post_ids.pop(first.name, None)
        record.publish.published_at = None
        record.status = PostStatus.APPROVED
        record.log("not published: every chosen platform failed; fix it and retry")
    return record


def adapters_for(destinations: list[str], local_dir: Path) -> list[PlatformAdapter]:
    """Destination keys (from the Outbox form) -> adapters, local folder always first
    so the record of what went out exists even if a platform fails."""
    out: list[PlatformAdapter] = [LocalFolderAdapter(local_dir)]
    wanted = set(destinations)
    if wanted & {"youtube_long", "youtube_short"}:
        from cap.publisher.youtube import YouTubeAdapter

        out.append(YouTubeAdapter())
    if wanted & {"facebook", "instagram"}:
        from cap.publisher.meta import MetaAdapter

        out.append(MetaAdapter())
    if "tiktok" in wanted:
        from cap.publisher.tiktok import TikTokAdapter

        out.append(TikTokAdapter())
    return out
