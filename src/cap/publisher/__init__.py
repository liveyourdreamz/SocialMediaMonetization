"""Publisher: plain code, no model anywhere near it (PRD 4.6, D4, D11, D23).

It receives a post record, its approval token and the chosen adapters. Before every
upload it asks `cap.release.authorise` whether this release may go to this adapter
(the human approval re-verified, the gates, canary and tour rules, the final render,
the manifest rebuilt from the files on disk, the token). The adapter receives the
verified manifest, never the record, so it can only send what was approved.

Duplicate uploads (review H5): one publish per post at a time (a second is refused
while the first runs); each destination's id and link are recorded and saved the
moment its upload returns; a retry skips destinations that already have an id.
"""

from __future__ import annotations

import json
import shutil
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from cap.approval.token import ApprovalToken
from cap.release import Manifest, PublishRefusedError, authorise, canonical_bytes, leaves_machine
from cap.schema.post_record import PostRecord, PostStatus

__all__ = [
    "DryRunAdapter",
    "LocalFolderAdapter",
    "PlatformAdapter",
    "PublishRefusedError",
    "Upload",
    "adapters_for",
    "publish",
    "publish_to",
]


@dataclass
class Upload:
    """What one adapter's upload produced. `also` holds other destinations the same
    upload created (Meta: the Instagram Reel next to the Facebook one); `errors` the
    ones it could not."""

    post_id: str
    url: str = ""
    also: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (id, url)
    errors: dict[str, str] = field(default_factory=dict)


class PlatformAdapter(Protocol):
    name: str

    def upload(self, release: Manifest) -> str | Upload:
        """Upload what the verified manifest names; return the platform post id."""


class DryRunAdapter:
    """Writes nothing anywhere. Used until the platform accounts exist."""

    name = "dry_run"
    leaves_machine = False

    def __init__(self) -> None:
        self.uploads: list[tuple[str, str]] = []

    def upload(self, release: Manifest) -> str:
        pid = f"dry-{release.post_id[:8]}"
        self.uploads.append((release.post_id, pid))
        return pid


class LocalFolderAdapter:
    """The laptop build's platform: copies every file of the release into
    <dir>/<post_id>/ with the manifest that was approved (manifest.json). The
    platform adapters replace it with the same one method."""

    name = "local"
    leaves_machine = False

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def upload(self, release: Manifest) -> str:
        out = self.directory / release.post_id
        out.mkdir(parents=True, exist_ok=True)
        for f in release.files:
            shutil.copy2(f.path, out / f.path.name)
        record = {
            "manifest": json.loads(canonical_bytes(release.document())),
            "manifest_sha256": release.sha256,
            "published_at": datetime.now(UTC).isoformat(),
        }
        (out / "manifest.json").write_text(
            json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8"
        )
        return f"local:{out}"


# ---------------------------------------------------------------- one publish per post

_POST_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


@contextmanager
def _one_at_a_time(post_id: str) -> Iterator[None]:
    with _LOCKS_GUARD:
        lock = _POST_LOCKS.setdefault(post_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise PublishRefusedError("a publish of this post is already running")
    try:
        yield
    finally:
        lock.release()


def _record(record: PostRecord, name: str, result: str | Upload) -> None:
    up = result if isinstance(result, Upload) else Upload(post_id=str(result))
    pub = record.publish
    pub.platform_post_ids[name] = up.post_id
    pub.errors.pop(name, None)
    if up.url:
        pub.urls[name] = up.url
    for other, (oid, ourl) in up.also.items():
        pub.platform_post_ids[other] = oid
        pub.errors.pop(other, None)
        if ourl:
            pub.urls[other] = ourl
    pub.errors.update(up.errors)
    record.log(f"published to {name} as {up.post_id}")


def publish_to(
    record: PostRecord,
    token: ApprovalToken | None,
    adapters: list[PlatformAdapter],
    save: Callable[[PostRecord], None] | None = None,
) -> PostRecord:
    """Every chosen destination in turn, each one authorised just before its upload.
    Every destination is authorised once up front too, so a refusal (a tour post on
    a platform, an expired token) sends nothing anywhere. One platform failing never
    undoes another; `save` persists the record after each result. The post is
    published once a platform took it (or, with no platform chosen, the local copy)."""
    if not adapters:
        raise PublishRefusedError("no destination chosen")
    persist = save or (lambda _r: None)
    with _one_at_a_time(record.post_id):
        for adapter in adapters:
            authorise(record, token, adapter)
        for adapter in adapters:
            if adapter.name in record.publish.platform_post_ids:
                record.log(f"{adapter.name}: already sent, not sent again")
                continue
            manifest = authorise(record, token, adapter)
            try:
                result = adapter.upload(manifest)
            except Exception as e:  # noqa: BLE001 - recorded per destination, shown on the Outbox
                record.publish.errors[adapter.name] = f"{type(e).__name__}: {str(e)[:200]}"
                record.log(f"{adapter.name} refused or failed: {type(e).__name__}")
            else:
                _record(record, adapter.name, result)
            persist(record)
        _settle(record, adapters)
        persist(record)
    return record


def _settle(record: PostRecord, adapters: list[PlatformAdapter]) -> None:
    ids = record.publish.platform_post_ids
    platforms = [a.name for a in adapters if leaves_machine(a)]
    took = any(n in ids for n in platforms) if platforms else any(a.name in ids for a in adapters)
    if took:
        record.publish.published_at = record.publish.published_at or datetime.now(UTC)
        record.status = PostStatus.PUBLISHED
    else:
        # a platform was chosen and none took it: the local copy is a record, not a
        # publication (review H3). Approved still, so a retry from the Outbox works.
        record.log("not published: every chosen platform failed; fix it and retry")


def publish(
    record: PostRecord, token: ApprovalToken | None, adapter: PlatformAdapter
) -> PostRecord:
    """One destination: the same rule as `publish_to`."""
    return publish_to(record, token, [adapter])


def adapters_for(destinations: list[str], local_dir: Path) -> list[PlatformAdapter]:
    """Destination keys (from the Outbox form) -> adapters, local folder always first
    so the record of what went out exists even if a platform fails."""
    out: list[PlatformAdapter] = [LocalFolderAdapter(local_dir)]
    wanted = set(destinations)
    if wanted & {"youtube_long", "youtube_short"}:
        from cap.publisher.youtube import YouTubeAdapter

        if "youtube_long" in wanted:
            out.append(YouTubeAdapter("long"))
        if "youtube_short" in wanted:
            out.append(YouTubeAdapter("short"))
    if wanted & {"facebook", "instagram"}:
        from cap.publisher.meta import MetaAdapter

        out.append(MetaAdapter())
    if "tiktok" in wanted:
        from cap.publisher.tiktok import TikTokAdapter

        out.append(TikTokAdapter())
    return out
