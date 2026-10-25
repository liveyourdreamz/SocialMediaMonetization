"""Try again: put a post that stopped on a failure back at the step it stopped on
(Hemant, 25 Oct: a Wi-Fi drop left a Falak draft stuck, with no way to restart it from
the screens; PRD 6 runbook step 3, "retry parked posts").

Only a post parked because a step failed after its retries, or was interrupted by a
restart, can be tried again: the cause may have gone (the network is back). A post
parked for a decision (escalated by the reviewer, injection suspected, made for kids
doubtful) cannot: trying again would not change the answer, and those are decided on
their own path. The step runs again with the inputs it had (its last job's payload),
saved and queued in one transaction.
"""

from __future__ import annotations

from typing import Any

from cap.jobs import Job, JobKind
from cap.schema.post_record import PostRecord, PostStatus
from cap.store import PostStore

FAILURE_REASONS = ("the pipeline could not run", "the tour pipeline could not run", "interrupted")
# the workflow step a post stopped at -> (the job that redoes it, the status it runs from)
STEP_JOB: dict[str, tuple[JobKind, PostStatus]] = {
    "brief": (JobKind.BRIEF, PostStatus.DRAFTING),
    "draft": (JobKind.BRIEF, PostStatus.DRAFTING),
    "research": (JobKind.CHECKS, PostStatus.GATED),
    "checks": (JobKind.CHECKS, PostStatus.GATED),
    "video": (JobKind.RENDER, PostStatus.RENDERING),
}


class TryAgainRefusedError(ValueError):
    """The post cannot be tried again; the message says why, in words."""


def can_try_again(record: PostRecord) -> bool:
    return record.status == PostStatus.PARKED and record.flags.stop_reason.startswith(
        FAILURE_REASONS
    )


def _last(jobs: list[Job], kind: JobKind) -> Job | None:
    return next((j for j in reversed(jobs) if j.kind == kind), None)


def plan(record: PostRecord, jobs: list[Job]) -> tuple[JobKind, PostStatus, dict[str, Any]]:
    """Which job redoes the stopped step, the status it runs from, and its inputs. A
    canary or tour post redoes its whole run; a step whose inputs were never recorded
    starts again from the brief."""
    if record.flags.canary:
        last = _last(jobs, JobKind.CANARY)
        return JobKind.CANARY, PostStatus.DRAFTING, dict(last.payload) if last else {}
    if record.flags.tour:
        return JobKind.TOUR, PostStatus.DRAFTING, {}
    kind, status = STEP_JOB.get(record.flags.stopped_at, STEP_JOB["draft"])
    last = _last(jobs, kind)
    if last is None and kind != JobKind.BRIEF:
        kind, status = STEP_JOB["draft"]
        last = _last(jobs, kind)
    payload = dict(last.payload) if last else {"urls": [], "test_log": ""}
    return kind, status, payload


def try_again(store: PostStore, post_id: str, who: str) -> JobKind:
    """Queue the stopped step again; TryAgainRefusedError when the post cannot be."""
    record = store.get(post_id)
    if not can_try_again(record):
        raise TryAgainRefusedError(
            "only a post that stopped on a failure can be tried again; this one "
            + ("is not stopped" if record.status != PostStatus.PARKED else "waits for a decision")
        )
    if store.jobs.active(post_id):
        raise TryAgainRefusedError("this post is already running")
    kind, status, payload = plan(record, store.jobs.jobs_for(post_id))
    step = record.flags.stopped_at or "draft"
    record.flags.stopped_at, record.flags.stop_reason = "", ""
    record.status = status
    record.log(f"trying again from the {step} step, asked by {who}")
    store.file(record, kind, payload)
    store.audit.append(
        "post tried again", post_id=post_id, detail={"step": step, "job": kind.value}
    )
    return kind
