"""The job queue in Postgres (cap.jobs has the rules; the indexes in 0002_jobs.sql
enforce them). Claiming uses FOR UPDATE SKIP LOCKED, so two workers never take the
same job; the one-running-per-post index stops two jobs writing one post at once.

A held job carries a session advisory lock (JOB_LOCKS, job id), taken in the claim's
own transaction and kept on that connection until the job ends. The database drops it
when the holding process dies, so `recover` can tell a dead worker's job from a live
one by trying the lock (review R6)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from psycopg import Connection, errors
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from cap.jobs import MAX_ATTEMPTS, Job, JobKind, JobQueue, JobStatus

_COLUMNS = "id, kind, post_id, payload, status, attempts, error, created_at, run_after"
JOB_LOCKS = 7031  # the advisory-lock namespace for held jobs (two-key form)
_CLAIM = (
    "UPDATE jobs SET status = 'running', attempts = attempts + 1,"
    " started_at = now() WHERE id = ("
    "  SELECT j.id FROM jobs j WHERE j.status = 'queued'"
    "   AND (j.run_after IS NULL OR j.run_after <= now()) AND NOT (j.kind = ANY(%s))"
    "   AND NOT EXISTS ("
    "   SELECT 1 FROM jobs r WHERE r.post_id = j.post_id AND r.status = 'running')"
    "  ORDER BY j.id FOR UPDATE SKIP LOCKED LIMIT 1)"
    f" RETURNING {_COLUMNS}"
)


def insert_job(conn: Connection, kind: JobKind, post_id: str, payload: dict[str, Any]) -> int:
    """Queue work on this connection (inside the caller's transaction when it has one);
    the id of the job already queued or running for that post and kind, if any."""
    row = conn.execute(
        "INSERT INTO jobs (kind, post_id, payload) VALUES (%s, %s, %s)"
        " ON CONFLICT (post_id, kind) WHERE status IN ('queued', 'running')"
        " DO NOTHING RETURNING id",
        (kind.value, post_id, Jsonb(payload)),
    ).fetchone()
    if row is None:  # the same work is already queued or running
        row = conn.execute(
            "SELECT id FROM jobs WHERE post_id = %s AND kind = %s"
            " AND status IN ('queued', 'running')",
            (post_id, kind.value),
        ).fetchone()
    assert row is not None
    return int(row[0])


def _job(row: tuple[Any, ...]) -> Job:
    return Job(
        id=row[0],
        kind=JobKind(row[1]),
        post_id=row[2],
        payload=dict(row[3]),
        status=JobStatus(row[4]),
        attempts=row[5],
        error=row[6],
        created_at=row[7],
        run_after=row[8],
    )


class PostgresJobQueue(JobQueue):
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def enqueue(self, kind: JobKind, post_id: str, payload: dict[str, Any] | None = None) -> int:
        with self._pool.connection() as conn:
            return insert_job(conn, kind, post_id, payload or {})

    def claim(self, skip: frozenset[JobKind] = frozenset()) -> Job | None:
        with self._pool.connection() as conn:
            try:
                row = conn.execute(_CLAIM, (_kinds(skip),)).fetchone()
            except errors.UniqueViolation:  # another worker just started this post
                return None
        return _job(row) if row else None

    def retry(self, job_id: int, error: str, after_s: float) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE jobs SET status = 'queued', error = %s, started_at = NULL,"
                " run_after = now() + make_interval(secs => %s) WHERE id = %s",
                (error[:500], after_s, job_id),
            )

    @contextmanager
    def held(self, skip: frozenset[JobKind] = frozenset()) -> Iterator[Job | None]:
        # one connection for the whole job: its advisory lock is the proof of life
        with self._pool.connection() as conn:
            try:
                with conn.transaction():
                    row = conn.execute(_CLAIM, (_kinds(skip),)).fetchone()
                    if row is not None:  # locked before the claim is visible to anyone
                        conn.execute("SELECT pg_advisory_lock(%s, %s)", (JOB_LOCKS, row[0]))
            except errors.UniqueViolation:  # another worker just started this post
                row = None
            if row is None:
                yield None
                return
            try:
                yield _job(row)
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s, %s)", (JOB_LOCKS, row[0]))

    def finish(self, job_id: int) -> None:
        self._close(job_id, JobStatus.DONE, "")

    def fail(self, job_id: int, error: str) -> None:
        self._close(job_id, JobStatus.FAILED, error[:500])

    def _close(self, job_id: int, status: JobStatus, error: str) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "UPDATE jobs SET status = %s, error = %s, finished_at = now() WHERE id = %s",
                (status.value, error, job_id),
            )

    def active(self, post_id: str, kind: JobKind | None = None) -> bool:
        sql = "SELECT 1 FROM jobs WHERE post_id = %s AND status IN ('queued', 'running')"
        params: tuple[Any, ...] = (post_id,)
        if kind is not None:
            sql += " AND kind = %s"
            params = (post_id, kind.value)
        with self._pool.connection() as conn:
            return conn.execute(sql + " LIMIT 1", params).fetchone() is not None  # type: ignore[arg-type]

    def recover(self) -> tuple[list[Job], list[Job]]:
        resumed: list[Job] = []
        given_up: list[Job] = []
        with self._pool.connection() as conn:
            running = conn.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE status = 'running' ORDER BY id"
            ).fetchall()
            for r in running:
                job = _job(r)
                taken = conn.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)", (JOB_LOCKS, job.id)
                ).fetchone()
                if not (taken and taken[0]):
                    continue  # a live worker holds it
                try:
                    row = _release(conn, job)
                finally:
                    conn.execute("SELECT pg_advisory_unlock(%s, %s)", (JOB_LOCKS, job.id))
                if row is not None:
                    (given_up if row.status == JobStatus.FAILED else resumed).append(row)
        return resumed, given_up

    def jobs_for(self, post_id: str) -> list[Job]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE post_id = %s ORDER BY id", (post_id,)
            ).fetchall()
        return [_job(r) for r in rows]


def _release(conn: Connection, job: Job) -> Job | None:
    """Put an abandoned running job back in the queue, or give it up after
    MAX_ATTEMPTS; None when it was no longer running."""
    if job.attempts >= MAX_ATTEMPTS:
        sql = (
            "UPDATE jobs SET status = 'failed', error = 'interrupted too many times',"
            " finished_at = now()"
        )
    else:
        sql = "UPDATE jobs SET status = 'queued', started_at = NULL"
    row = conn.execute(
        f"{sql} WHERE id = %s AND status = 'running' RETURNING {_COLUMNS}", (job.id,)
    ).fetchone()
    return _job(row) if row else None


def _kinds(skip: frozenset[JobKind]) -> list[str]:
    return sorted(k.value for k in skip)
