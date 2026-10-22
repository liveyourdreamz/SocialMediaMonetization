"""The final render, after approval and off the request thread (review C1).

Why: the review render is made before anyone has written the opening line, so it can
only show a placeholder. When the approver approves, this job renders the package
again with the line spoken and shown (about a minute with video, instant with the
text renderer), then seals the release: records the final files, builds the
manifest and issues the token bound to it (cap.release.seal). Until then the post is
approved with release status "preparing" and the publisher refuses it. A failure
leaves it approved without a token, with the reason on the record and a retry on
the Outbox.

One job per post at a time; a second start while one runs returns the running one.
"""

from __future__ import annotations

import threading
from typing import Protocol

from cap import release
from cap.render.package import render_root, render_stage
from cap.schema.post_record import Decision, PostRecord, PostStatus, Release, ReleaseStatus


class _Store(Protocol):
    def put(self, record: PostRecord) -> None: ...


_RUNNING: dict[str, threading.Thread] = {}
_GUARD = threading.Lock()


def is_running(post_id: str) -> bool:
    with _GUARD:
        t = _RUNNING.get(post_id)
        return bool(t and t.is_alive())


def can_start(record: PostRecord) -> bool:
    """Approved, not sealed, nothing running: the Outbox offers "make the final
    video" (first time for a post approved before releases existed, or a retry)."""
    return (
        record.release.status != ReleaseStatus.READY
        and not is_running(record.post_id)
        and record.approval.decision == Decision.APPROVE
        and record.status == PostStatus.APPROVED
    )


def start(store: _Store, record: PostRecord) -> threading.Thread:
    """Begin the final render for an approved post. Raises PublishRefusedError when
    the approval does not hold or the release is already sealed."""
    with _GUARD:
        running = _RUNNING.get(record.post_id)
        if running is not None and running.is_alive():
            return running
        if record.release.status == ReleaseStatus.READY:
            raise release.PublishRefusedError(
                "the final video is ready; re-issue the token instead"
            )
        release.check_approval(record)
        record.release = Release(
            status=ReleaseStatus.PREPARING, attempts=record.release.attempts + 1
        )
        record.approval.token = ""  # nothing publishable until the new release is sealed
        record.log("preparing the final video with your opening line")
        store.put(record)
        t = threading.Thread(
            target=_run, args=(store, record), name=f"final-{record.post_id[:8]}", daemon=True
        )
        _RUNNING[record.post_id] = t
        t.start()
        return t


def wait(post_id: str, timeout: float | None = None) -> None:
    """Block until the post's final render (if any) finishes: the CLI and tests."""
    with _GUARD:
        t = _RUNNING.get(post_id)
    if t is not None:
        t.join(timeout)


def _run(store: _Store, record: PostRecord) -> None:
    try:
        rendered = render_stage(record, render_root(record), "final", record.asset.kind)
        release.seal(record, rendered.files)
        record.log("approved: the final video is ready; waiting in the Outbox")
    except Exception as e:  # noqa: BLE001 - every failure is shown with a retry
        why = str(e) if isinstance(e, release.PublishRefusedError) else f"{type(e).__name__}: {e}"
        record.release.status = ReleaseStatus.FAILED
        record.release.error = why[:300]
        record.approval.token = ""
        record.log(f"final video failed: {why[:200]}; retry from the Outbox")
    finally:
        store.put(record)
        with _GUARD:
            _RUNNING.pop(record.post_id, None)
