"""The job queue in Postgres (cap.jobs has the rules; the indexes in 0002_jobs.sql
enforce them). Claiming uses FOR UPDATE SKIP LOCKED, so two workers never take the
same job; the one-running-per-post index stops two jobs writing one post at once."""

from __future__ import annotations

from typing import Any

from psycopg import errors
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from cap.jobs import MAX_ATTEMPTS, Job, JobKind, JobQueue, JobStatus

_COLUMNS = "id, kind, post_id, payload, status, attempts, error, created_at"


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
    )


class PostgresJobQueue(JobQueue):
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def enqueue(self, kind: JobKind, post_id: str, payload: dict[str, Any] | None = None) -> int:
        with self._pool.connection() as conn:
            row = conn.execute(
                "INSERT INTO jobs (kind, post_id, payload) VALUES (%s, %s, %s)"
                " ON CONFLICT (post_id, kind) WHERE status IN ('queued', 'running')"
                " DO NOTHING RETURNING id",
                (kind.value, post_id, Jsonb(payload or {})),
            ).fetchone()
            if row is None:  # the same work is already queued or running
                row = conn.execute(
                    "SELECT id FROM jobs WHERE post_id = %s AND kind = %s"
                    " AND status IN ('queued', 'running')",
                    (post_id, kind.value),
                ).fetchone()
        assert row is not None
        return int(row[0])

    def claim(self) -> Job | None:
        with self._pool.connection() as conn:
            try:
                row = conn.execute(
                    "UPDATE jobs SET status = 'running', attempts = attempts + 1,"
                    " started_at = now() WHERE id = ("
                    "  SELECT j.id FROM jobs j WHERE j.status = 'queued' AND NOT EXISTS ("
                    "   SELECT 1 FROM jobs r WHERE r.post_id = j.post_id AND r.status = 'running')"
                    "  ORDER BY j.id FOR UPDATE SKIP LOCKED LIMIT 1)"
                    f" RETURNING {_COLUMNS}"
                ).fetchone()
            except errors.UniqueViolation:  # another worker just started this post
                return None
        return _job(row) if row else None

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
        with self._pool.connection() as conn, conn.transaction():
            given_up = conn.execute(
                "UPDATE jobs SET status = 'failed', error = 'interrupted too many times',"
                f" finished_at = now() WHERE status = 'running' AND attempts >= %s"
                f" RETURNING {_COLUMNS}",
                (MAX_ATTEMPTS,),
            ).fetchall()
            resumed = conn.execute(
                "UPDATE jobs SET status = 'queued', started_at = NULL"
                f" WHERE status = 'running' RETURNING {_COLUMNS}"
            ).fetchall()
        return [_job(r) for r in resumed], [_job(r) for r in given_up]

    def jobs_for(self, post_id: str) -> list[Job]:
        with self._pool.connection() as conn:
            rows = conn.execute(
                f"SELECT {_COLUMNS} FROM jobs WHERE post_id = %s ORDER BY id", (post_id,)
            ).fetchall()
        return [_job(r) for r in rows]
