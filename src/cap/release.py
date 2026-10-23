"""The release: everything that leaves the machine for one post, and the one rule that
decides whether it may (review C2, C3).

Why a manifest. The approval token used to bind the long-form file alone, so the
Short, the thumbnail, every title, caption, description and tag, and the platform
flags could change after approval without anyone noticing (Meta and TikTok upload
only the Short). The manifest is a canonical, deterministic JSON document of every
file by role with its SHA-256 and every text and flag an adapter sends. The token
binds the manifest's SHA-256; the publisher rebuilds the manifest from the record
and the files on disk and refuses on any difference; adapters upload from the
verified `Manifest`, never from the record.

Why one function. "May this be released?" used to be answered by the post's status
field plus a few checks in `publish()`, and `publish_to` skipped them for every
adapter after the first. `authorise()` is now the only answer: the human approval
(re-verified from the record, not trusted from the status), the gates
(`cap.domain.verdict.gates_cleared`, never re-implemented here), the canary and tour rules,
the final render, the manifest and the token. The publisher calls it before every
upload; `seal()` and `reissue()` call the approval half before issuing a token.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from cap.config import get_settings
from cap.domain import token as tok
from cap.domain.approval import approval_refusal
from cap.policy import AI_ROLE_DISCLOSURE
from cap.schema.post_record import (
    Decision,
    Platform,
    PostRecord,
    ReleaseFile,
    ReleaseStatus,
)

MANIFEST_SCHEMA = "cap.release/1"
Role = Literal["long", "short", "thumbnail"]
Cut = Literal["long", "short"]
# platforms that publish the vertical cut; everything else gets the long-form text
_SHORT_PLATFORMS = {Platform.YOUTUBE_SHORT.value, Platform.FACEBOOK.value, Platform.INSTAGRAM.value}


class PublishRefusedError(RuntimeError):
    """The release may not go out; the message says why, in Hemant's words."""


class Destination(Protocol):
    """What `authorise()` needs to know about an adapter. `leaves_machine` is False
    only for the local folder and the dry run; an adapter that does not say is
    treated as a real platform (fail safe)."""

    name: str


def leaves_machine(adapter: Destination | None) -> bool:
    return bool(getattr(adapter, "leaves_machine", True)) if adapter is not None else True


# ---------------------------------------------------------------- the manifest


@dataclass(frozen=True)
class ManifestFile:
    role: Role
    path: Path
    sha256: str


@dataclass(frozen=True)
class VariantText:
    platform: str
    title: str
    caption: str
    description: str
    tags: tuple[str, ...]


@dataclass(frozen=True)
class PlatformFlags:
    made_for_kids: bool
    synthetic_media: bool
    paid_promotion: bool


@dataclass(frozen=True)
class Manifest:
    """What an adapter may send. Paths are where the bytes are on this machine; they
    are not part of the hashed document (moving the data folder must not void an
    approval), the file names and hashes are."""

    post_id: str
    channel: str
    approved_by: str
    opening_line: str
    files: tuple[ManifestFile, ...]
    variants: tuple[VariantText, ...]
    flags: PlatformFlags
    sha256: str = field(default="", compare=False)

    def file(self, role: Role) -> ManifestFile | None:
        return next((f for f in self.files if f.role == role), None)

    def video(self, cut: Cut) -> ManifestFile:
        """The file for a cut; a platform asking for the Short gets the long-form
        file when the post has no Short (what the adapters did before)."""
        f = self.file(cut) or self.file("long")
        if f is None:
            raise PublishRefusedError("the release has no video file")
        return f

    def text_for(self, platform: str) -> VariantText:
        """The title, caption, description and tags for one platform: its own
        variant if the post has one, else the cut it publishes, else the long form."""
        by = {v.platform: v for v in self.variants}
        if platform in by:
            return by[platform]
        if platform in _SHORT_PLATFORMS or platform == "tiktok":
            if Platform.YOUTUBE_SHORT.value in by:
                return by[Platform.YOUTUBE_SHORT.value]
        return by.get(Platform.YOUTUBE_LONG.value) or self.variants[0]

    def document(self) -> dict[str, Any]:
        """The canonical document the token binds (no local paths)."""
        return {
            "schema": MANIFEST_SCHEMA,
            "post_id": self.post_id,
            "channel": self.channel,
            "approved_by": self.approved_by,
            "opening_line": self.opening_line,
            "files": {
                f.role: {"name": f.path.name, "sha256": f.sha256}
                for f in sorted(self.files, key=lambda f: f.role)
            },
            "variants": [
                {
                    "platform": v.platform,
                    "title": v.title,
                    "caption": v.caption,
                    "description": v.description,
                    "tags": list(v.tags),
                }
                for v in self.variants
            ],
            "flags": {
                "made_for_kids": self.flags.made_for_kids,
                "synthetic_media": self.flags.synthetic_media,
                "paid_promotion": self.flags.paid_promotion,
            },
        }


def canonical_bytes(document: dict[str, Any]) -> bytes:
    """Sorted keys, no whitespace, UTF-8 kept as is: the same document always hashes
    the same, on any machine."""
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def description_for(record: PostRecord, caption: str | None = None) -> str:
    """The published description: the caption, the links that confirmed a claim
    (never one that confirmed nothing, review H2) and the AI-character disclosure."""
    from cap.domain.verdict import confirmed_sources

    text = (record.draft.caption if caption is None else caption).strip()
    sources = confirmed_sources(record)
    lines = [text, ""]
    if sources:
        lines += ["Sources:", *sources, ""]
    if AI_ROLE_DISCLOSURE not in text:
        lines.append(AI_ROLE_DISCLOSURE)
    lines.append(
        "Voice and visuals are synthetic; the script is reviewed and approved by a person before publishing."
    )
    return "\n".join(lines).strip()


def tags_for(record: PostRecord) -> tuple[str, ...]:
    return tuple([t for t in record.brief.topic.split() if len(t) > 3][:10])


def flags_for(record: PostRecord) -> PlatformFlags:
    """The platform declarations, from the gates and the record (D12, R-16, PRD 4.6)."""
    d = record.gates.disclosure
    return PlatformFlags(
        made_for_kids=bool(d.made_for_kids),
        synthetic_media=bool(d.ai_label or record.media_manifest.synthetic_elements_present),
        paid_promotion=bool(d.paid_promo or record.sponsor is not None),
    )


def _variants(record: PostRecord) -> tuple[VariantText, ...]:
    tags = tags_for(record)
    out = [
        VariantText(
            platform=v.platform.value,
            title=v.title or record.draft.title,
            caption=v.caption or record.draft.caption,
            description=description_for(record, v.caption or record.draft.caption),
            tags=tags,
        )
        for v in record.variants
    ]
    if not any(v.platform == Platform.YOUTUBE_LONG.value for v in out):
        out.insert(
            0,
            VariantText(
                platform=Platform.YOUTUBE_LONG.value,
                title=record.draft.title,
                caption=record.draft.caption,
                description=description_for(record),
                tags=tags,
            ),
        )
    return tuple(out)


def build_manifest(record: PostRecord) -> Manifest:
    """The manifest from the record's final files as they are on disk now. A file
    that is missing refuses: nothing goes out that cannot be hashed."""
    files = []
    for rf in record.release.files:
        path = Path(rf.uri)
        if not path.is_file():
            raise PublishRefusedError(f"the final {rf.role} file is missing: {path.name}")
        files.append(ManifestFile(role=rf.role, path=path, sha256=tok.sha256_file(path)))
    if not any(f.role == "long" for f in files):
        raise PublishRefusedError("the release has no long-form file")
    m = Manifest(
        post_id=record.post_id,
        channel=record.channel_id.value,
        approved_by=record.approval.reviewer,
        opening_line=record.human_contribution.text.strip(),
        files=tuple(sorted(files, key=lambda f: f.role)),
        variants=_variants(record),
        flags=flags_for(record),
    )
    digest = hashlib.sha256(canonical_bytes(m.document())).hexdigest()
    return replace(m, sha256=digest)


def release_files(paths: dict[Role, Path]) -> list[ReleaseFile]:
    """The record's view of the final files (role, where, hash at render time)."""
    return [
        ReleaseFile(role=role, uri=str(p), sha256=tok.sha256_file(p))
        for role, p in sorted(paths.items())
    ]


# ---------------------------------------------------------------- may it be released


def check_approval(record: PostRecord) -> None:
    """The human approval, re-verified from what the card recorded (never from the
    status alone) plus the gates. Raises PublishRefusedError with the reason."""
    why = approval_refusal(record, get_settings())
    if why:
        raise PublishRefusedError(why)


def authorise(
    record: PostRecord, token: tok.ApprovalToken | None, adapter: Destination | None
) -> Manifest:
    """The one answer to "may this be released to this adapter?". Returns the
    verified manifest the adapter uploads from; raises PublishRefusedError."""
    check_approval(record)
    if record.flags.tour and leaves_machine(adapter):
        raise PublishRefusedError("a guided-tour post never leaves this computer")
    if record.release.status != ReleaseStatus.READY:
        raise PublishRefusedError(
            f"the final video is not ready (release {record.release.status.value})"
        )
    if token is None:
        raise PublishRefusedError("no approval token on the record")
    manifest = build_manifest(record)
    if manifest.sha256 != record.release.manifest_sha256:
        raise PublishRefusedError("the release changed since it was approved")
    if token.reviewer != record.approval.reviewer:
        raise PublishRefusedError("the token names a different reviewer from the card")
    ok, reason = tok.verify(token, record.post_id, manifest.sha256)
    if not ok:
        raise PublishRefusedError(f"token rejected: {reason}")
    return manifest


def decode_token(record: PostRecord) -> tok.ApprovalToken | None:
    """The record's token, or None when there is none or it cannot be read."""
    if not record.approval.token:
        return None
    try:
        return tok.ApprovalToken.decode(record.approval.token)
    except ValueError:
        return None


def token_state(record: PostRecord) -> Literal["none", "valid", "expired", "invalid"]:
    """For the Outbox: whether the held token would still verify."""
    t = decode_token(record)
    if t is None:
        return "none" if not record.approval.token else "invalid"
    ok, reason = tok.verify(t, record.post_id, record.release.manifest_sha256)
    if ok:
        return "valid"
    return "expired" if reason == tok.EXPIRED else "invalid"


# ---------------------------------------------------------------- issuing


def _issue(record: PostRecord, manifest: Manifest) -> tok.ApprovalToken:
    t = tok.issue(
        record.post_id,
        manifest.sha256,
        record.approval.reviewer,
        Decision.APPROVE.value,
        canary=record.flags.canary,
    )
    record.approval.token = t.encode()
    return t


def seal(record: PostRecord, files: dict[Role, Path]) -> tok.ApprovalToken:
    """After the final render: record the files, build the manifest, issue the token
    bound to it, mark the release ready. The approval is re-verified first."""
    check_approval(record)
    record.release.files = release_files(files)
    manifest = build_manifest(record)
    record.release.manifest_sha256 = manifest.sha256
    t = _issue(record, manifest)
    record.release.status = ReleaseStatus.READY
    record.release.error = ""
    record.release.prepared_at = datetime.now(UTC)
    record.log(f"final release sealed: manifest {manifest.sha256[:12]}, token issued")
    return t


def reissue(record: PostRecord, reviewer: str) -> tok.ApprovalToken:
    """M4: a fresh token for a release that is still exactly what was approved (an
    expired token cannot be extended; this re-verifies and signs anew). Only the
    approver who approved the post may ask."""
    if reviewer != record.approval.reviewer:
        raise PublishRefusedError("only the approver who approved this post can re-issue")
    check_approval(record)
    if record.release.status != ReleaseStatus.READY:
        raise PublishRefusedError("there is no final release to re-issue a token for")
    manifest = build_manifest(record)
    if manifest.sha256 != record.release.manifest_sha256:
        raise PublishRefusedError("the release changed since it was approved")
    t = _issue(record, manifest)
    record.log(f"token re-issued by {reviewer} for manifest {manifest.sha256[:12]}")
    return t
