"""Records that have been through the approval path, for publisher and token tests.

Built by hand rather than through the pipeline so a test can break exactly one thing,
but with every field the card sets on an approve (decision, decided_at, the opening
line, the reviewer) and a release sealed by the real `cap.release.seal`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from cap import release
from cap.approval import token as tok
from cap.schema.post_record import (
    Channel,
    Decision,
    Platform,
    PostRecord,
    PostStatus,
    StageOneReview,
    Variant,
    Verdict,
)

OPENING_LINE = "Here is the thing about sync."


def approved(tmp_path: Path, channel: Channel = Channel.A, tour: bool = False) -> PostRecord:
    """Approved on the card, gates passed, release not yet made."""
    r = PostRecord(channel_id=channel, status=PostStatus.APPROVED)
    r.flags.tour = tour
    r.brief.topic = "Notely seven day test"
    r.draft.title = "Notely after seven days"
    r.draft.caption = "What a week of notes taught us. Presented by an AI character."
    r.variants = [
        Variant(platform=Platform.YOUTUBE_LONG, title=r.draft.title, caption=r.draft.caption),
        Variant(platform=Platform.YOUTUBE_SHORT, title="Notely in 60s", caption="Short cut."),
    ]
    r.gates.originality.passed = True
    r.gates.ethics.verdict = Verdict.PASS
    r.gates.disclosure.passed = True
    r.gates.facts.passed = True
    r.human_contribution.text = OPENING_LINE
    r.approval.decision = Decision.APPROVE
    r.approval.pre_reveal_decision = Decision.APPROVE
    r.approval.decided_at = datetime.now(UTC)
    r.approval.reviewer = "hemant"
    if channel == Channel.C:
        r.approval.stage_one = StageOneReview(reviewer="layla", decision=Decision.APPROVE)
    return r


def final_files(tmp_path: Path, r: PostRecord) -> dict[release.Role, Path]:
    d = tmp_path / "final" / r.post_id
    d.mkdir(parents=True, exist_ok=True)
    files: dict[release.Role, Path] = {}
    for role, name, body in (
        ("long", "long.mp4", b"long-form bytes"),
        ("short", "short.mp4", b"short bytes"),
        ("thumbnail", "thumbnail.png", b"thumbnail bytes"),
    ):
        p = d / name
        p.write_bytes(body)
        files[role] = p  # type: ignore[index]
    return files


def sealed(
    tmp_path: Path, channel: Channel = Channel.A, tour: bool = False
) -> tuple[PostRecord, tok.ApprovalToken, dict[release.Role, Path]]:
    """Approved and sealed: final files on disk, manifest hashed, token issued."""
    r = approved(tmp_path, channel, tour)
    files = final_files(tmp_path, r)
    t = release.seal(r, files)
    return r, t, files
