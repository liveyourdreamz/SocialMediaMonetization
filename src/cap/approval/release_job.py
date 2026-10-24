"""The final render, after approval, as a queued job (review C1; M1 step 3).

Why: the review render is made before anyone has written the opening line, so it can
only show a placeholder. When the approver approves, `begin` marks the release
"preparing" and queues a RELEASE job; the worker runs `run`, which renders the package
again with the line spoken and shown, then seals the release: records the final
files, builds the manifest and issues the token bound to it (cap.release.seal). Until
then the post is approved without a token and the publisher refuses it. A failure
leaves it approved without a token, with the reason on the record and a retry on the
Outbox. A restart mid-render runs the job again (cap.jobs.recover).

The queue allows one release job per post at a time.
"""

from __future__ import annotations

from pathlib import Path

from cap import release
from cap.config import get_settings
from cap.jobs import Job, JobKind
from cap.render.package import render_root, render_stage
from cap.schema.post_record import Decision, PostRecord, PostStatus, Release, ReleaseStatus
from cap.store import PostStore


def is_running(store: PostStore, post_id: str) -> bool:
    return store.jobs.active(post_id, JobKind.RELEASE)


def can_start(record: PostRecord) -> bool:
    """Approved, and the release neither sealed nor being made: the Outbox offers "make
    the final video" (first time for a post approved before releases existed, or a
    retry). `begin` is safe to call twice: the queue holds one release job per post."""
    return (
        record.release.status in (ReleaseStatus.NONE, ReleaseStatus.FAILED)
        and record.approval.decision == Decision.APPROVE
        and record.status == PostStatus.APPROVED
    )


def begin(store: PostStore, record: PostRecord) -> None:
    """Queue the final render for an approved post. Raises PublishRefusedError when the
    approval does not hold or the release is already sealed; does nothing more when
    the job is already queued or running."""
    if is_running(store, record.post_id):
        return
    if record.release.status == ReleaseStatus.READY:
        raise release.PublishRefusedError("the final video is ready; re-issue the token instead")
    release.check_approval(record)
    record.release = Release(status=ReleaseStatus.PREPARING, attempts=record.release.attempts + 1)
    record.approval.token = ""  # nothing publishable until the new release is sealed
    record.log("preparing the final video with your opening line")
    store.file(record, JobKind.RELEASE, {})


def run(store: PostStore, job: Job) -> None:
    """The worker's handler. Rendering takes a while, so the result is applied to the
    post as it stands when it finishes, not to the copy read at the start."""
    record = store.get(job.post_id)
    try:
        rendered = render_stage(record, render_root(record), "final", record.asset.kind)
        signed = _sign(record, list(rendered.files.values()))
    except Exception as e:  # noqa: BLE001 - every failure is shown with a retry
        failed(store, job.post_id, f"{type(e).__name__}: {e}")
        return

    def sealed(r: PostRecord) -> None:
        try:
            release.seal(r, rendered.files)
            r.release.c2pa_signed = signed
            if signed:
                r.log(f"C2PA provenance signed: {', '.join(signed)} (self-signed, D29)")
            r.log("approved: the final video is ready; waiting in the Outbox")
        except release.PublishRefusedError as e:
            _mark_failed(r, str(e))

    store.update(job.post_id, sealed)


def _sign(record: PostRecord, files: list[Path]) -> list[str]:
    """D29: the C2PA manifest goes in before the seal, so the token binds the signed
    files. A signing failure fails the release (retry on the Outbox): an unsigned
    file never goes out while signing is on."""
    if get_settings().c2pa == "off":
        return []
    from cap.render.provenance import sign_release

    return sign_release(record.draft.title, record.post_id, record.channel_id.value, files)


def failed(store: PostStore, post_id: str, why: str) -> None:
    store.update(post_id, lambda r: _mark_failed(r, why))


def _mark_failed(record: PostRecord, why: str) -> None:
    record.release.status = ReleaseStatus.FAILED
    record.release.error = why[:300]
    record.approval.token = ""
    record.log(f"final video failed: {why[:200]}; retry from the Outbox")
