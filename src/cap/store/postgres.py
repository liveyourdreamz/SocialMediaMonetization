"""The Postgres store (M1, ADR-006): the laptop's own Postgres now, Cloud SQL later,
same schema and migrations.

The full post record is stored as JSONB and read back through `load_record`, so a row
written by an earlier build is migrated on read, exactly like the old JSON files. A row
this build cannot read is skipped and reported (`unreadable`), never deleted, and the
rest of the app carries on (review H8).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from cap.gates.history import HistoryStore, eligible
from cap.schema.migrate import load_record
from cap.schema.post_record import PostRecord, PostStatus
from cap.store import (
    AppState,
    PostConflictError,
    PostNotFoundError,
    PostStore,
    migrations,
    saved_detail,
)
from cap.store.pgaudit import PostgresAuditLog
from cap.store.pgaudit import append as audit_append
from cap.store.pgideas import PostgresIdeaBook
from cap.store.pgjobs import PostgresJobQueue

_log = logging.getLogger("cap.store")


def connect(url: str, *, max_size: int = 8) -> ConnectionPool:
    """A small pool; every connection autocommits unless a block opens a transaction."""
    pool = ConnectionPool(
        url,
        min_size=1,
        max_size=max_size,
        kwargs={"autocommit": True},
        open=False,
    )
    pool.open(wait=True, timeout=30)
    return pool


class PostgresStore(PostStore):
    def __init__(self, pool: ConnectionPool) -> None:
        super().__init__(PostgresJobQueue(pool), PostgresAuditLog(pool), PostgresIdeaBook(pool))
        self._pool = pool
        with self._pool.connection() as conn:
            applied = migrations.apply(conn)
            if applied:
                _log.info("database migrated: %s", ", ".join(applied))

    @classmethod
    def open(cls, url: str) -> PostgresStore:
        return cls(connect(url))

    def close(self) -> None:
        self._pool.close()

    def __enter__(self) -> PostgresStore:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- posts ------------------------------------------------------------------------

    def get(self, post_id: str) -> PostRecord:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT record FROM posts WHERE post_id = %s", (post_id,)).fetchone()
        if row is None:
            raise PostNotFoundError(post_id)
        rec = self._load(post_id, row[0])
        if rec is None:
            raise PostNotFoundError(post_id)
        return rec

    def put(self, record: PostRecord) -> None:
        with self._pool.connection() as conn, conn.transaction():
            # the save and its audit entry commit together, or neither does
            row = conn.execute(
                "SELECT jsonb_array_length(record->'history') FROM posts"
                " WHERE post_id = %s FOR UPDATE",
                (record.post_id,),
            ).fetchone()
            _save(conn, record)
            audit_append(
                conn, "post saved", record.post_id, saved_detail(record, row[0] if row else 0)
            )

    def all(self) -> list[PostRecord]:
        return self._select("SELECT post_id, record FROM posts")

    def count(self) -> int:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT count(*) FROM posts").fetchone()
        return int(row[0]) if row else 0

    def queue(self) -> list[PostRecord]:
        return self._select(
            "SELECT post_id, record FROM posts WHERE status = %s ORDER BY created_at",
            (PostStatus.AWAITING_APPROVAL.value,),
        )

    def _select(self, sql: str, params: tuple[Any, ...] = ()) -> list[PostRecord]:
        with self._pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()  # type: ignore[arg-type]
        out = [self._load(pid, data) for pid, data in rows]
        return [r for r in out if r is not None]

    def _load(self, post_id: str, data: Any) -> PostRecord | None:
        try:
            loaded = load_record(json.dumps(data))
        except ValueError as e:
            if post_id not in self.unreadable:
                self.unreadable.append(post_id)
                _log.warning("post %s cannot be read by this build; left as is: %s", post_id, e)
            return None
        if loaded.dropped:
            _log.info("%s: set aside fields this build does not know: %s", post_id, loaded.dropped)
        return loaded.record

    # ---- card timers ------------------------------------------------------------------

    def _card_opened_at(self, post_id: str, reviewer: str) -> datetime | None:
        with self._pool.connection() as conn:
            row = conn.execute(
                "SELECT opened_at FROM card_opens WHERE post_id = %s AND reviewer = %s",
                (post_id, reviewer),
            ).fetchone()
        return row[0] if row else None

    def _set_card_opened_at(self, post_id: str, reviewer: str, at: datetime) -> None:
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO card_opens (post_id, reviewer, opened_at) VALUES (%s, %s, %s)"
                " ON CONFLICT (post_id, reviewer) DO NOTHING",
                (post_id, reviewer, at),
            )

    # ---- app state and history --------------------------------------------------------

    @property
    def state(self) -> AppState:
        with self._pool.connection() as conn:
            row = conn.execute("SELECT state FROM app_state WHERE id = 1").fetchone()
        return _state(row[0] if row else {})

    def update_state(self, change: Callable[[AppState], None]) -> AppState:
        """One transaction holding the row lock: a change made by another process in
        between is never overwritten (review R7)."""
        with self._pool.connection() as conn, conn.transaction():
            conn.execute(
                "INSERT INTO app_state (id, state) VALUES (1, '{}') ON CONFLICT (id) DO NOTHING"
            )
            row = conn.execute("SELECT state FROM app_state WHERE id = 1 FOR UPDATE").fetchone()
            state = _state(row[0] if row else {})
            change(state)
            conn.execute(
                "UPDATE app_state SET state = %s WHERE id = 1",
                (Jsonb({k: getattr(state, k) for k in _STATE_KEYS}),),
            )
        return state

    def history(self) -> HistoryStore:
        return PostgresHistory(self._pool)

    @contextmanager
    def exclusive(self, name: str) -> Iterator[None]:
        """A session advisory lock: one holder across every process on this database."""
        with self._pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtext(%s))", (name,))
            try:
                yield
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtext(%s))", (name,))


_STATE_KEYS = ("paused", "paused_reason", "analytics", "analytics_note", "canary_gap")


def _state(saved: dict[str, Any]) -> AppState:
    return AppState(**{k: v for k, v in saved.items() if k in _STATE_KEYS})


def _save(conn: Connection, record: PostRecord) -> None:
    """Insert a new post or update a stored one, only from its current revision."""
    nxt = record.revision + 1
    data = Jsonb(record.model_copy(update={"revision": nxt}).model_dump(mode="json"))
    if record.revision == 0:
        cur = conn.execute(
            "INSERT INTO posts (post_id, channel, status, created_at, revision, record)"
            " VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (post_id) DO NOTHING",
            (
                record.post_id,
                record.channel_id.value,
                record.status.value,
                record.created_at,
                nxt,
                data,
            ),
        )
    else:
        cur = conn.execute(
            "UPDATE posts SET channel = %s, status = %s, revision = %s, record = %s,"
            " updated_at = now() WHERE post_id = %s AND revision = %s",
            (
                record.channel_id.value,
                record.status.value,
                nxt,
                data,
                record.post_id,
                record.revision,
            ),
        )
    if cur.rowcount != 1:
        raise PostConflictError(record.post_id)
    record.revision = nxt


class PostgresHistory:
    """The originality gate's index of approved posts (PRD 4.12), in Postgres."""

    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def recent(self, channel_id: str, days: int) -> list[tuple[str, str]]:
        cutoff = datetime.now(UTC) - timedelta(days=days)
        with self._pool.connection() as conn:
            rows = conn.execute(
                "SELECT post_id, script FROM originality_history WHERE approved_at >= %s"
                " ORDER BY approved_at",
                (cutoff,),
            ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def add(self, record: PostRecord) -> bool:
        if not eligible(record):
            return False
        at = record.approval.decided_at or record.created_at
        with self._pool.connection() as conn:
            conn.execute(
                "INSERT INTO originality_history (post_id, channel, script, approved_at)"
                " VALUES (%s, %s, %s, %s) ON CONFLICT (post_id) DO NOTHING",
                (record.post_id, record.channel_id.value, record.draft.script, at),
            )
        return True
