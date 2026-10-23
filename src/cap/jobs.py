"""The job queue (M1 step 3, ADR-006): background work as recorded jobs, not threads.

Filing a brief, making the final video and running the guided tour each enqueue a job;
a worker inside the app (cap.worker) claims and runs them. Because jobs are rows, a
restart loses nothing: a job that was running when the app stopped is put back in the
queue and runs again (`recover`), a few times at most.

Rules every queue keeps (tests/test_jobs.py runs them against each adapter):

* one active job per post and kind: enqueueing the same work again returns the one
  already queued or running;
* one running job per post at a time, so two jobs never write the same post at once;
* oldest first.
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MAX_ATTEMPTS = 3  # a job interrupted this many times is given up and its post parked


class JobKind(StrEnum):
    BRIEF = "brief"  # a filed brief's first step: write the script and variants
    CHECKS = "checks"  # research and the four gates
    RENDER = "render"  # the review cut; the post reaches the approval queue
    RELEASE = "release"  # the final render and seal after approval
    TOUR = "tour"  # the guided tour's worked example


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


ACTIVE = (JobStatus.QUEUED, JobStatus.RUNNING)


@dataclass
class Job:
    id: int
    kind: JobKind
    post_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.QUEUED
    attempts: int = 0  # how many times a worker has started it
    error: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class JobQueue(ABC):
    @abstractmethod
    def enqueue(self, kind: JobKind, post_id: str, payload: dict[str, Any] | None = None) -> int:
        """Queue work for a post; returns the job id (the existing one when the same
        kind of work is already queued or running for that post)."""

    @abstractmethod
    def claim(self) -> Job | None:
        """The oldest queued job whose post has nothing running, now marked running."""

    @abstractmethod
    def finish(self, job_id: int) -> None: ...

    @abstractmethod
    def fail(self, job_id: int, error: str) -> None: ...

    @abstractmethod
    def active(self, post_id: str, kind: JobKind | None = None) -> bool:
        """Is work queued or running for this post (of this kind)?"""

    @abstractmethod
    def recover(self) -> tuple[list[Job], list[Job]]:
        """At start-up: jobs left running by a stopped app go back in the queue.
        Returns (resumed, given_up); a job past MAX_ATTEMPTS is failed, not resumed."""

    @abstractmethod
    def jobs_for(self, post_id: str) -> list[Job]:
        """Every job a post has had, oldest first."""

    def wait(self, post_id: str, timeout: float = 60.0) -> bool:
        """Block until the post has no queued or running job: the CLI and tests.
        True when it became idle in time."""
        deadline = time.monotonic() + timeout
        while self.active(post_id):
            if time.monotonic() > deadline:
                return False
            time.sleep(0.05)
        return True


class MemoryJobQueue(JobQueue):
    """In memory, same rules; nothing survives the process (tests, the offline check)."""

    def __init__(self) -> None:
        self._jobs: list[Job] = []
        self._lock = threading.Lock()

    def enqueue(self, kind: JobKind, post_id: str, payload: dict[str, Any] | None = None) -> int:
        with self._lock:
            for j in self._jobs:
                if j.post_id == post_id and j.kind == kind and j.status in ACTIVE:
                    return j.id
            job = Job(len(self._jobs) + 1, kind, post_id, dict(payload or {}))
            self._jobs.append(job)
            return job.id

    def claim(self) -> Job | None:
        with self._lock:
            busy = {j.post_id for j in self._jobs if j.status == JobStatus.RUNNING}
            for j in self._jobs:
                if j.status == JobStatus.QUEUED and j.post_id not in busy:
                    j.status = JobStatus.RUNNING
                    j.attempts += 1
                    return Job(**{**j.__dict__, "payload": dict(j.payload)})
            return None

    def _set(self, job_id: int, status: JobStatus, error: str = "") -> None:
        with self._lock:
            j = self._jobs[job_id - 1]
            j.status, j.error = status, error

    def finish(self, job_id: int) -> None:
        self._set(job_id, JobStatus.DONE)

    def fail(self, job_id: int, error: str) -> None:
        self._set(job_id, JobStatus.FAILED, error[:500])

    def active(self, post_id: str, kind: JobKind | None = None) -> bool:
        with self._lock:
            return any(
                j.post_id == post_id and j.status in ACTIVE and (kind is None or j.kind == kind)
                for j in self._jobs
            )

    def recover(self) -> tuple[list[Job], list[Job]]:
        resumed, given_up = [], []
        with self._lock:
            for j in self._jobs:
                if j.status != JobStatus.RUNNING:
                    continue
                if j.attempts >= MAX_ATTEMPTS:
                    j.status, j.error = JobStatus.FAILED, "interrupted too many times"
                    given_up.append(j)
                else:
                    j.status = JobStatus.QUEUED
                    resumed.append(j)
        return resumed, given_up

    def jobs_for(self, post_id: str) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs if j.post_id == post_id]
