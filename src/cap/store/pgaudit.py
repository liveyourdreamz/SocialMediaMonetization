"""The chained audit log in Postgres (cap.audit has the rules; 0003_audit.sql the
triggers that keep the table append-only)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from cap.audit import AuditLog, ChainCheck, Entry, current_actor, entry_hash, verify_chain

_LOCK_KEY = 0x415544  # "AUD": appends take turns, so each sees the true last hash
_COLUMNS = "id, at, actor, action, post_id, detail, prev_hash, hash"


def _entry(row: tuple[Any, ...]) -> Entry:
    return Entry(row[0], row[1], row[2], row[3], row[4], dict(row[5]), row[6], row[7])


def append(conn: Connection, action: str, post_id: str, detail: dict[str, Any]) -> Entry:
    """Write one entry on `conn`, inside the caller's transaction when there is one
    (a post save and its audit entry commit together)."""
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        last = conn.execute("SELECT id, hash FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()
        if last is None:
            anchor = conn.execute("SELECT prev_hash, first_id FROM audit_anchor").fetchone()
            assert anchor is not None
            prev, id_ = anchor[0], anchor[1]
        else:
            prev, id_ = last[1], last[0] + 1
        at, actor = datetime.now(UTC), current_actor()
        h = entry_hash(prev, id_, at, actor, action, post_id, detail)
        conn.execute(
            f"INSERT INTO audit_log ({_COLUMNS}) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (id_, at, actor, action, post_id, Jsonb(detail), prev, h),
        )
    return Entry(id_, at, actor, action, post_id, detail, prev, h)


class PostgresAuditLog(AuditLog):
    def __init__(self, pool: ConnectionPool) -> None:
        self._pool = pool

    def append(self, action: str, post_id: str = "", detail: dict[str, Any] | None = None) -> Entry:
        with self._pool.connection() as conn:
            return append(conn, action, post_id, dict(detail or {}))

    def entries(self, post_id: str | None = None) -> list[Entry]:
        sql, params = f"SELECT {_COLUMNS} FROM audit_log", tuple[str, ...]()
        if post_id is not None:
            sql, params = sql + " WHERE post_id = %s", (post_id,)
        with self._pool.connection() as conn:
            return [_entry(r) for r in conn.execute(sql + " ORDER BY id", params).fetchall()]  # type: ignore[arg-type]

    def verify(self) -> ChainCheck:
        with self._pool.connection() as conn, conn.transaction():
            anchor = conn.execute("SELECT prev_hash, first_id FROM audit_anchor").fetchone()
            rows = conn.execute(f"SELECT {_COLUMNS} FROM audit_log ORDER BY id").fetchall()
        if anchor is None:
            return ChainCheck(False, len(rows), None, "the chain's anchor row is missing")
        return verify_chain([_entry(r) for r in rows], anchor[0], anchor[1])

    def purge(self, older_than_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=older_than_days)
        with self._pool.connection() as conn, conn.transaction():
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
            last = conn.execute(
                "SELECT id, hash FROM audit_log WHERE at < %s ORDER BY id DESC LIMIT 1", (cutoff,)
            ).fetchone()
            if last is None:
                return 0
            conn.execute("SET LOCAL cap.audit_purge = 'on'")
            gone = conn.execute("DELETE FROM audit_log WHERE id <= %s", (last[0],)).rowcount
            conn.execute(
                "UPDATE audit_anchor SET prev_hash = %s, first_id = %s WHERE id = 1",
                (last[1], last[0] + 1),
            )
        return gone
