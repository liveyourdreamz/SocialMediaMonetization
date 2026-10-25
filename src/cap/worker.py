"""The worker inside the app (M1 step 3, ADR-006): it runs the queued jobs.

`cap serve` and the smoke run start one with the web app; the cloud will run the
same loop as its own service. At start-up it recovers first:

* jobs left running when the app stopped go back in the queue and run again, and the
  post says so ("resumed after a restart");
* a job interrupted too often (cap.jobs.MAX_ATTEMPTS) is given up and its post parked;
* a post stuck mid-step with no job to finish it (made by a build before the queue)
  is parked with the step it was on, as before; a final render left "preparing" is
  queued again.

A handler that raises fails its job and the failure lands on the post: a final render
is marked failed with a retry on the Outbox; anything else parks the post with the
reason. A failure never stops the worker (D43).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime

from cap import alerts
from cap.approval import release_job
from cap.audit import acting_as
from cap.config import get_settings
from cap.demo.canary_job import run_canary_job
from cap.demo.ideas_job import run_ideas_job, schedule_due
from cap.demo.seed import run_checks_job, run_render_job, run_tour, run_write_job
from cap.domain.cost import Spend, carried_by, charge, metering, total
from cap.domain.queue import drafting_pause
from cap.jobs import MAX_ATTEMPTS, Job, JobKind, JobStatus
from cap.schema.post_record import PostRecord, PostStatus, ReleaseStatus
from cap.store import IN_FLIGHT, PostStore, park_if_interrupted

Handler = Callable[[PostStore, Job], None]
_log = logging.getLogger("cap.worker")
TICK_S = 60.0  # how often the clock looks for scheduled work
RETRY_BASE_S = 60.0  # the first retry's pause; each later one is four times longer
DRAFTING = frozenset({JobKind.BRIEF, JobKind.CANARY})  # what the ADR-016 pause holds back


def default_handlers() -> dict[JobKind, Handler]:
    return {
        JobKind.BRIEF: run_write_job,
        JobKind.CHECKS: run_checks_job,
        JobKind.RENDER: run_render_job,
        JobKind.RELEASE: release_job.run,
        JobKind.TOUR: run_tour,
        JobKind.IDEAS: run_ideas_job,
        JobKind.CANARY: run_canary_job,
    }


class Worker:
    def __init__(
        self,
        store: PostStore,
        handlers: dict[JobKind, Handler] | None = None,
        threads: int = 2,
        poll_s: float = 0.2,
    ) -> None:
        self.store = store
        self.handlers = handlers or default_handlers()
        self.threads = threads
        self.poll_s = poll_s
        self._stop = threading.Event()
        self._running: list[threading.Thread] = []
        self.drafting_paused = ""  # why new drafting is paused (ADR-016), set by the clock

    # ---- start-up ---------------------------------------------------------------------

    def reclaim(self) -> None:
        """Jobs a dead worker left running go back in the queue (start-up and the clock;
        a live worker's job is never taken, cap.jobs)."""
        resumed, given_up = self.store.jobs.recover()
        for job in resumed:
            self._note(
                job.post_id,
                f"resumed after a restart: {job.kind.value} (attempt {job.attempts + 1})",
            )
        for job in given_up:
            self._failed(job, "interrupted by restarts too many times; file it again")

    def recover(self) -> None:
        """At start-up: reclaim, then settle posts no job will finish."""
        self.reclaim()
        for rec in self.store.all():
            if self.store.jobs.active(rec.post_id):
                continue
            if rec.status in IN_FLIGHT:
                self.store.update(rec.post_id, park_if_interrupted)
            elif (
                rec.status == PostStatus.APPROVED and rec.release.status == ReleaseStatus.PREPARING
            ):
                # a final render a build before the queue left unfinished: make it again
                self.store.jobs.enqueue(JobKind.RELEASE, rec.post_id)
                self._note(rec.post_id, "resumed after a restart: release")

    def start(self) -> Worker:
        self.recover()
        for i in range(self.threads):
            # the clock runs in the first thread only (review R3: one tick per minute)
            t = threading.Thread(
                target=self._loop, args=(i == 0,), name=f"cap-worker-{i}", daemon=True
            )
            t.start()
            self._running.append(t)
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        for t in self._running:
            t.join(timeout)

    # ---- the loop ---------------------------------------------------------------------

    def tick(self) -> None:
        """The clock: take back jobs a dead process left, decide whether new drafting is
        paused (ADR-016), and queue work that is due by the time of day (the weekly ideas
        run)."""
        try:
            self.reclaim()
            self._check_drafting_pause()
            for key in schedule_due(self.store):
                _log.info("queued %s", key)
        except Exception:  # noqa: BLE001 - the clock never stops the worker
            _log.exception("could not check the schedule")

    def _loop(self, clock: bool = True) -> None:
        last_tick = 0.0
        while not self._stop.is_set():
            if clock and time.monotonic() - last_tick >= TICK_S:
                last_tick = time.monotonic()
                self.tick()
            try:
                worked = self.run_once()
            except Exception:  # the queue itself failed (database gone): wait, retry
                _log.exception("the job queue could not be read")
                worked = False
            if not worked:
                self._stop.wait(self.poll_s)

    def _check_drafting_pause(self) -> None:
        why = drafting_pause(
            self.store.queue(), datetime.now(UTC), get_settings().queue_idle_pause_hours
        )
        if why and not self.drafting_paused:
            self._alert("New drafts paused", why + ". Open the queue to clear it.")
        elif self.drafting_paused and not why:
            self.store.audit.append("new drafts resumed")
        self.drafting_paused = why

    def run_once(self) -> bool:
        """Claim and run one job, held for as long as it runs; False when there was
        nothing to do. While new drafting is paused, no brief or canary starts."""
        skip = DRAFTING if self.drafting_paused else frozenset()
        with self.store.jobs.held(skip) as job:
            if job is None:
                return False
            self._run(job)
        return True

    def _run(self, job: Job) -> None:
        """Run the handler, metered: calls a pipeline step charged to its post stay
        there; any other call the job made (the canary writer, trend research), and
        every call of a step that raised, is charged here, so nothing paid for goes
        unrecorded (D44, review R9)."""
        handler = self.handlers.get(job.kind)
        with metering() as spent:
            try:
                if handler is None:
                    raise LookupError(f"no handler for {job.kind.value} jobs")
                with acting_as("worker"):
                    handler(self.store, job)
            except Exception as e:  # noqa: BLE001 - recorded on the job and the post (D43)
                why = f"{type(e).__name__}: {str(e)[:300]}"
                _log.exception("job %s (%s) failed", job.id, job.kind.value)
                self._charge(job, [*spent, *carried_by(e)])
                if not self._replaced(job):
                    self._retry_or_fail(job, why)
                return
        self._charge(job, list(spent))
        if not self._replaced(job):
            self.store.jobs.finish(job.id)

    def _replaced(self, job: Job) -> bool:
        """Hemant restarted this step while it hung (cap.demo.retry): a fresh job has
        taken over, so this one's ending is not recorded and nothing runs twice."""
        if self.store.jobs.status_of(job.id) == JobStatus.RUNNING:
            return False
        _log.info("job %s (%s) was restarted while it ran; its result is dropped",
                  job.id, job.kind.value)  # fmt: skip
        return True

    def _retry_or_fail(self, job: Job, why: str) -> None:
        """PRD 3: a failure is tried again after a pause (1 minute, then 4) before the
        post is parked with the reason (review R12)."""
        if job.attempts <= min(get_settings().job_retries, MAX_ATTEMPTS - 1):
            delay = RETRY_BASE_S * 4 ** (job.attempts - 1)
            self.store.jobs.retry(job.id, why, delay)
            line = f"{job.kind.value} failed ({why[:160]}); trying again in {delay // 60:.0f} min"
            if job.kind == JobKind.IDEAS:
                self.store.audit.append(
                    "ideas run retrying", detail={"job": job.post_id, "why": why}
                )
            else:
                self._note(job.post_id, line)
            return
        self.store.jobs.fail(job.id, why)
        self._failed(job, why)
        what = "This week's trend research" if job.kind == JobKind.IDEAS else f"Post {job.post_id}"
        self._alert(f"{job.kind.value} failed", f"{what} stopped after {job.attempts} tries: {why}")

    def _alert(self, subject: str, body: str) -> None:
        """Email Hemant when set up; always in the audit log (the Operator screen)."""
        sent = alerts.send(subject, body)
        self.store.audit.append("alert", detail={"subject": subject, "body": body, "sent": sent})

    def _charge(self, job: Job, spent: list[Spend]) -> None:
        if not spent:
            return
        try:
            if job.kind == JobKind.IDEAS:  # no post behind it: the audit log keeps it
                detail = {"job": job.post_id, "usd": total(spent), "calls": len(spent)}
                self.store.audit.append("ideas run cost", detail=detail)
                return
            budget = get_settings().post_budget_usd
            self.store.update(job.post_id, lambda r: charge(r, spent, budget))
        except Exception:  # noqa: BLE001 - a charge never stops the worker; logged
            _log.exception("could not charge job %s's model calls", job.id)

    # ---- what a failure means for the post ------------------------------------------------

    def _failed(self, job: Job, why: str) -> None:
        if job.kind == JobKind.IDEAS:  # no post behind it: the audit log keeps the reason
            self.store.audit.append("ideas run failed", detail={"job": job.post_id, "why": why})
            return
        try:
            if job.kind == JobKind.RELEASE:
                release_job.failed(self.store, job.post_id, why)
            else:
                self.store.update(job.post_id, lambda r: _park(r, job.kind, why))
        except Exception:  # noqa: BLE001 - the post may be gone; the job keeps the reason
            _log.exception("could not record the failure on post %s", job.post_id)

    def _note(self, post_id: str, line: str) -> None:
        try:
            self.store.update(post_id, lambda r: r.log(line))
        except Exception:  # noqa: BLE001 - a note never stops recovery
            _log.exception("could not note on post %s", post_id)


def _park(record: PostRecord, kind: JobKind, why: str) -> None:
    what = "the tour pipeline" if kind == JobKind.TOUR else "the pipeline"
    step = IN_FLIGHT.get(record.status, "draft")
    record.stop(step, f"{what} could not run: {why}", PostStatus.PARKED)
    record.log(f"parked: {what} could not run: {why}")


def drain(store: PostStore, post_id: str | None = None, timeout: float = 600.0) -> None:
    """Run queued jobs in this thread until the post (or, with no post, the whole
    queue) is idle: the full check, and tests that want a job finished now. Safe beside
    a running worker: a job it holds is waited for, never run twice."""
    w = Worker(store)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ran = w.run_once()
        if post_id is not None and not store.jobs.active(post_id):
            return
        if post_id is None and not ran:
            return
        if not ran:
            time.sleep(0.05)
    raise TimeoutError(f"jobs for {post_id or 'the queue'} still running after {timeout:.0f}s")
